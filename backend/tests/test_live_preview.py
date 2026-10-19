"""Живий перегляд: `POST/DELETE /api/preview` і канал `/ws/live` (§9.4, §9.5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config as C
from app.main import app
from app.services import live_stream


@pytest.fixture(scope="module")
def client():
    """TestClient **як контекстний менеджер** — і це тут принципово.

    Без `with` Starlette піднімає окремий event loop на кожен запит, тож
    asyncio-задача прев'ю, створена під час `POST /api/preview`, гине разом із
    тим циклом і жоден кадр не доходить до `/ws/live`. Контекстний менеджер
    тримає один портал на весь модуль — так само, як справжній uvicorn.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _no_leftover_preview(client):
    """Кожен тест починає й завершує з порожнім каналом."""
    client.delete("/api/preview")
    yield
    client.delete("/api/preview")


def test_preview_requires_creature(client):
    res = client.post("/api/preview", json={"source": "demo"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_preview_reports_network_sizes(client, demo_creature):
    res = client.post("/api/preview", json={"source": "demo", "creature": demo_creature})
    assert res.status_code == 200
    body = res.json()
    assert body["preview_id"].startswith("pv-")
    assert body["source"] == "demo"
    assert body["n_joints"] == 4
    assert body["n_in"] == C.n_in_from_joints(4)
    assert body["genome_size"] == C.genome_size(body["n_in"], 4)
    assert body["dt"] == C.DT and body["control_every"] == C.CONTROL_EVERY


def test_sources_without_data_are_rejected(client, demo_creature, monkeypatch):
    """Усі джерела реалізовані (етап 4), але без даних жодне не дає 200."""
    from app.services.sim_manager import manager as sim
    monkeypatch.setattr(sim, "cfg", None)
    monkeypatch.setattr(sim, "latest_best", None)
    res = client.post("/api/preview", json={"source": "live_training"})
    assert res.status_code == 409
    res = client.post("/api/preview", json={
        "source": "genome", "creature": demo_creature, "genome": None})
    assert res.status_code == 422
    res = client.post("/api/preview", json={"source": "best", "run_id": "немає__20260101-0000"})
    assert res.status_code == 404


def test_preview_state_round_trip(client, demo_creature):
    assert client.get("/api/preview").json()["active"] is False
    client.post("/api/preview", json={"source": "demo", "creature": demo_creature})
    state = client.get("/api/preview").json()
    assert state["active"] is True and state["source"] == "demo"
    assert client.get("/api/simulation/status").json()["mode"] == "live"
    assert client.delete("/api/preview").json()["active"] is False
    assert client.get("/api/simulation/status").json()["mode"] == "headless"


def test_scene_packet_is_sent_once_and_frames_follow(client, demo_creature):
    """Статика сцени — окремим пакетом при підключенні, у кадрах її немає (§9.5)."""
    env = {
        "obstacles": [
            {"type": "static_circle", "radius": 1.5, "position": [-8, 2]},
            {"type": "moving_polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
             "p1": [0, -10], "p2": [0, 10], "period": 12.0, "phase": 0.0},
        ],
        # Маршрут далеко від старту в центрі: спіймання перезапустило б епізод
        # і надіслало б другий `scene` замість кадру
        "predators": [{"waypoints": [[-15, -16], [15, -16], [15, -13]]}],
    }
    client.post("/api/preview", json={
        "source": "demo", "creature": demo_creature, "environment": env})

    with client.websocket_connect("/ws/live") as ws:
        scene = ws.receive_json()
        assert scene["type"] == "scene"
        assert scene["world"] == {"width": C.W_WORLD, "height": C.H_WORLD}
        assert [o["type"] for o in scene["obstacles_static"]] == ["static_circle"]
        assert scene["obstacles_dynamic"][0]["id"] == 1
        assert scene["waypoints"] == [[[-15, -16], [15, -16], [15, -13]]]
        assert scene["creature"]["shape"] == "circle"

        frame = ws.receive_json()
        assert frame["type"] == "frame"
        assert "obstacles_static" not in frame and "world" not in frame
        assert len(frame["creature"]["segments"]) == 4
        assert len(frame["creature"]["joint_outputs"]) == 4
        assert frame["predators"][0]["r_aura"] == C.R_AURA
        assert frame["obstacles_dynamic"][0]["id"] == 1
        assert frame["sim_speed"] == 1.0


def test_frames_advance_simulation_time(client, demo_creature):
    client.post("/api/preview", json={"source": "demo", "creature": demo_creature})
    with client.websocket_connect("/ws/live") as ws:
        ws.receive_json()                       # scene
        times = [ws.receive_json()["t"] for _ in range(5)]
    assert times == sorted(times)
    assert times[-1] > times[0]
    # Темп 1.0 → два кроки DT на кадр (60 fps при DT = 1/120).
    # Допуск — за точністю `t` у кадрі: воно округлене до 4 знаків (§9.5)
    assert times[1] - times[0] == pytest.approx(2 * C.DT, abs=1e-4)


def test_moving_obstacle_actually_moves(client, demo_creature):
    env = {"obstacles": [{
        "type": "moving_polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
        "p1": [0, -10], "p2": [0, 10], "period": 2.0, "phase": 0.0}]}
    client.post("/api/preview", json={
        "source": "demo", "creature": demo_creature, "environment": env, "speed": 4.0})
    with client.websocket_connect("/ws/live") as ws:
        ws.receive_json()
        ys = [ws.receive_json()["obstacles_dynamic"][0]["y"] for _ in range(12)]
    assert max(ys) - min(ys) > 0.1


def test_second_client_gets_cached_scene(client, demo_creature):
    client.post("/api/preview", json={"source": "demo", "creature": demo_creature})
    with client.websocket_connect("/ws/live") as first:
        assert first.receive_json()["type"] == "scene"
        with client.websocket_connect("/ws/live") as second:
            assert second.receive_json()["type"] == "scene"


def test_starting_a_preview_replaces_the_previous_one(client, demo_creature):
    first = client.post("/api/preview", json={
        "source": "demo", "creature": demo_creature}).json()["preview_id"]
    second = client.post("/api/preview", json={
        "source": "demo", "creature": demo_creature}).json()["preview_id"]
    assert first != second
    assert live_stream.manager.current.preview_id == second


def test_hub_keeps_only_newest_frame():
    """Відстаючий клієнт має бачити найновіший кадр, а не хвіст застарілих (§9.5)."""
    hub = live_stream.LiveHub()
    queue = hub.subscribe()
    for i in range(5):
        hub.publish_frame({"type": "frame", "t": i})
    assert queue.qsize() == C.LIVE_QUEUE_DEPTH
    assert queue.get_nowait()["t"] == 4


def test_steps_per_frame_follows_speed(demo_creature):
    from app.core.world import World
    from app.schemas import CreatureConfig, EnvironmentConfig

    def make(k):
        return World(CreatureConfig.model_validate(demo_creature), EnvironmentConfig())

    hub = live_stream.LiveHub()
    assert live_stream.PreviewSession(hub, make, source="demo", speed=1.0).steps_per_frame == 2
    assert live_stream.PreviewSession(hub, make, source="demo", speed=4.0).steps_per_frame == 8
    # Повільний темп не може зупинити симуляцію зовсім
    assert live_stream.PreviewSession(hub, make, source="demo", speed=0.1).steps_per_frame >= 1
