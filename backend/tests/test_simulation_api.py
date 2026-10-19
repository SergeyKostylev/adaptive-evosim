"""Керування ГА через REST і `/ws/metrics` (§9.4, §9.6).

Потік ГА публікує події через `call_soon_threadsafe`, тож потрібен живий
event loop — лише `with TestClient(app) as client` (див. test_live_preview).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import config as C
from app.main import app
from app.services.sim_manager import manager as sim


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "WORKERS_EVAL", 1)       # без пулу процесів: швидко й тихо
    with TestClient(app) as c:
        yield c
        sim.stop(timeout=10)


def experiment(demo_creature, generations=3, episode_seconds=0.5) -> dict:
    return {"name": "api-test", "seed": 3, "creature": demo_creature,
            "ga": {"population": 6, "generations": generations,
                   "episode_seconds": episode_seconds}}


def wait_state(client, state: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/simulation/status").json()
        if status["state"] == state:
            return status
        time.sleep(0.05)
    raise AssertionError(f"не дочекались {state}: {status}")


def test_full_run_publishes_generations_and_stores_results(client, demo_creature):
    with client.websocket_connect("/ws/metrics") as ws:
        assert ws.receive_json()["type"] == "state"

        r = client.post("/api/simulation/start", json=experiment(demo_creature))
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        assert r.json()["state"] == "RUNNING"

        generations, states = [], []
        while True:
            packet = ws.receive_json()
            if packet["type"] == "generation":
                generations.append(packet)
            else:
                states.append(packet["state"])
                if packet["state"] == "STOPPED":
                    assert packet["archived"] == f"{C.ARCHIVE_SUBDIR}/{run_id}"
                    break

    assert [g["generation"] for g in generations] == [1, 2, 3]
    assert all(g["run_id"] == run_id for g in generations)
    assert set(generations[0]["best_components"]) == set(C.FITNESS_KEYS)
    assert "RUNNING" in states

    status = client.get("/api/simulation/status").json()
    assert status["state"] == "STOPPED" and status["generation"] == 3
    assert status["run_id"] == run_id and status["population"] == 6

    rows = client.get("/api/metrics/generations", params={"run_id": run_id}).json()["rows"]
    assert [r["generation"] for r in rows] == [1, 2, 3]
    assert rows[-1]["best_fitness"] == generations[-1]["best_fitness"]
    tail = client.get("/api/metrics/generations",
                      params={"run_id": run_id, "from_gen": 3}).json()["rows"]
    assert len(tail) == 1

    # Завершений прогін — в архіві (§11), без чекпоінтів
    archived = C.DATA_DIR / C.ARCHIVE_SUBDIR / run_id
    assert states[-1] == "STOPPED"
    assert sorted(p.name for p in archived.iterdir()) == sorted(C.ARCHIVE_FILES)
    assert (archived / "metrics.csv").read_bytes() == (
        C.DATA_DIR / C.RUNS_SUBDIR / run_id / "metrics.csv").read_bytes()

    genome = client.get("/api/genome/best", params={"run_id": run_id}).json()
    validated = client.post("/api/creature/validate", json=demo_creature).json()
    assert genome["creature_hash"] == validated["creature_hash"]
    assert len(genome["weights"]) == validated["genome_size"]


def test_second_start_conflicts(client, demo_creature):
    first = client.post("/api/simulation/start", json=experiment(demo_creature, generations=200))
    assert first.status_code == 200
    second = client.post("/api/simulation/start", json=experiment(demo_creature))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"
    run_id = first.json()["run_id"]
    assert client.post("/api/simulation/stop").json()["state"] == "STOPPED"
    # Зупинений до кінця — лише локально в runs/, не в архіві
    assert not (C.DATA_DIR / C.ARCHIVE_SUBDIR / run_id).exists()
    assert (C.DATA_DIR / C.RUNS_SUBDIR / run_id / "config.json").exists()
    assert client.get("/api/simulation/status").json()["state"] == "STOPPED"


def test_pause_holds_generations_resume_continues(client, demo_creature):
    client.post("/api/simulation/start", json=experiment(demo_creature, generations=500))
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)

    assert client.post("/api/simulation/pause").json()["state"] == "PAUSED"
    time.sleep(0.3)                     # поточне покоління дооцінюється
    held = client.get("/api/simulation/status").json()["generation"]
    time.sleep(0.5)
    status = client.get("/api/simulation/status").json()
    assert status["state"] == "PAUSED" and status["generation"] == held

    assert client.post("/api/simulation/pause").status_code == 409
    assert client.post("/api/simulation/resume").json()["state"] == "RUNNING"
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] <= held:
        assert time.monotonic() < deadline
        time.sleep(0.05)

    stopped = client.post("/api/simulation/stop").json()
    assert stopped["state"] == "STOPPED"


def test_preview_best_genome_while_training(client, demo_creature):
    """DoD етапу 3: перегляд найкращої особини наживо без зупинки навчання."""
    run_id = client.post("/api/simulation/start",
                         json=experiment(demo_creature, generations=500)).json()["run_id"]
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)

    r = client.post("/api/preview", json={"source": "best", "run_id": run_id})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "best"
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["type"] == "scene"
        frame = ws.receive_json()
        assert frame["type"] == "frame"
        assert frame["hud"]["fitness_running"] is not None
        assert any(abs(u) > 0 for u in frame["creature"]["joint_outputs"]) or frame["t"] < 0.1

    before = client.get("/api/simulation/status").json()["generation"]
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] <= before:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.get("/api/simulation/status").json()["state"] == "RUNNING"
    client.delete("/api/preview")
    client.post("/api/simulation/stop")


def test_live_training_follows_latest_generation(client, demo_creature):
    """Перемикач Headless ↔ Live (§9.4): прев'ю поточного найкращого, навчання не спиняється."""
    client.post("/api/simulation/start", json=experiment(demo_creature, generations=500))
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)

    r = client.post("/api/preview", json={"source": "live_training", "speed": 8.0})
    assert r.status_code == 200, r.text
    assert r.json()["episode_seconds"] == 0.5          # горизонт прогону, не 30 с прев'ю
    assert client.get("/api/simulation/status").json()["mode"] == "live"
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["source"] == "live_training"
        episodes, generations = set(), set()
        while len(episodes) < 3:
            packet = ws.receive_json()
            if packet["type"] == "frame":
                episodes.add(packet["episode"])
                generations.add(packet["hud"]["generation"])
    assert None not in generations and min(generations) >= 1

    before = client.get("/api/simulation/status").json()["generation"]
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] <= before:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.delete("/api/preview").json()["active"] is False
    status = client.get("/api/simulation/status").json()
    assert status["state"] == "RUNNING" and status["mode"] == "headless"
    client.post("/api/simulation/stop")


def test_preview_genome_from_body(client, demo_creature):
    validated = client.post("/api/creature/validate", json=demo_creature).json()
    genome = {"creature_hash": validated["creature_hash"], "n_in": validated["n_in"],
              "n_joints": validated["n_joints"], "weights": [0.0] * validated["genome_size"]}
    r = client.post("/api/preview", json={"source": "genome", "creature": demo_creature,
                                          "genome": genome})
    assert r.status_code == 200, r.text
    client.delete("/api/preview")

    genome["creature_hash"] = "sha256:чужий"
    r = client.post("/api/preview", json={"source": "genome", "creature": demo_creature,
                                          "genome": genome})
    assert r.status_code == 422


def test_metrics_for_unknown_or_malicious_run_id(client):
    assert client.get("/api/metrics/generations",
                      params={"run_id": "немає__20260101-0000"}).status_code == 404
    assert client.get("/api/genome/best", params={"run_id": "../../etc"}).status_code == 404
