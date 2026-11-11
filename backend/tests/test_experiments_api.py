"""Прогони, продовження й експорт через REST (§9.4, §11, DoD етапу 5)."""

from __future__ import annotations

import csv
import io
import re
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app import config as C
from app.main import app
from app.services.experiment_store import METRICS_FIELDS
from app.services.sim_manager import manager as sim
from tests.test_simulation_api import experiment, wait_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "WORKERS_EVAL", 1)
    monkeypatch.setattr(C, "GA_CHECKPOINT_EVERY", 2)
    with TestClient(app) as c:
        yield c
        sim.stop(timeout=10)


def finished_run(client, demo_creature, generations=3) -> str:
    run_id = client.post("/api/simulation/start",
                         json=experiment(demo_creature, generations)).json()["run_id"]
    wait_state(client, "STOPPED")
    return run_id


def runs(client) -> dict[str, dict]:
    r = client.get("/api/experiments")
    assert r.status_code == 200
    return {run["run_id"]: run for run in r.json()["runs"]}


def test_list_reports_finished_run(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    run = runs(client)[run_id]
    assert run["state"] == "COMPLETED" and run["location"] == "runs"
    assert run["generations_done"] == run["generations_total"] == 3
    assert run["archived"] is True and run["last_checkpoint"] == 3
    assert run["name"] == "api-test" and run["created_at"].startswith("20")
    assert run["fitness_weights"]["survival"] == C.W_SURVIVAL
    rows = client.get("/api/metrics/generations", params={"run_id": run_id}).json()["rows"]
    best = max(rows, key=lambda r: r["best_fitness"])
    assert run["best_fitness"] == best["best_fitness"]
    assert run["best_generation"] == best["generation"]


def test_export_csv_is_valid(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    r = client.get(f"/api/experiments/{run_id}/export", params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f'filename="{run_id}.csv"' in r.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(r.text)))
    assert list(table[0]) == [*METRICS_FIELDS, "eval_seconds", "sim_fps_equivalent"]
    assert [int(row["generation"]) for row in table] == [1, 2, 3]
    for row in table:
        assert all(float(row[k]) == float(row[k]) for k in METRICS_FIELDS)   # числа, не NaN


def test_export_pdf_has_a_page_per_chart_and_png_is_png(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    pdf = client.get(f"/api/experiments/{run_id}/export", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.content))
    assert pages == len(C.PLOT_CHARTS)
    png = client.get(f"/api/experiments/{run_id}/export",
                     params={"format": "png", "chart": "components"})
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    figures = C.DATA_DIR / C.RUNS_SUBDIR / run_id / C.FIGURES_SUBDIR
    assert {p.name for p in figures.iterdir()} >= {"fitness.png", "fitness.pdf", "figures.pdf"}


def test_export_rejects_bad_parameters(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    assert client.get(f"/api/experiments/{run_id}/export", params={"format": "xls"}).status_code == 400
    assert client.get(f"/api/experiments/{run_id}/export",
                      params={"format": "png", "chart": "pie"}).status_code == 400
    assert client.get("/api/experiments/nope__20260101-0000/export").status_code == 404
    assert client.get("/api/experiments/..%2F..%2Fetc/export").status_code == 404


def test_archive_only_run_is_listed_and_exportable(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    shutil.rmtree(C.DATA_DIR / C.RUNS_SUBDIR / run_id)          # як після git clone
    run = runs(client)[run_id]
    assert run["location"] == "archive" and run["last_checkpoint"] is None
    r = client.get(f"/api/experiments/{run_id}/export", params={"format": "csv"})
    assert r.status_code == 200 and r.text.count("\n") == 4
    assert client.get("/api/genome/best", params={"run_id": run_id}).status_code == 200
    # Продовжувати архівну копію нема з чого
    r = client.post("/api/simulation/continue", json={"run_id": run_id, "generations": 5})
    assert r.status_code == 404


def test_continue_extends_a_finished_run(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    assert client.post("/api/simulation/continue", json={"run_id": run_id}).status_code == 409

    r = client.post("/api/simulation/continue", json={"run_id": run_id, "generations": 5})
    assert r.status_code == 200 and r.json() == {"state": "RUNNING", "run_id": run_id}
    status = wait_state(client, "STOPPED")
    assert status["generation"] == 5 and status["generations_total"] == 5

    rows = client.get("/api/metrics/generations", params={"run_id": run_id}).json()["rows"]
    assert [r["generation"] for r in rows] == [1, 2, 3, 4, 5]
    run = runs(client)[run_id]
    assert run["state"] == "COMPLETED" and run["generations_total"] == 5
    archived = C.DATA_DIR / C.ARCHIVE_SUBDIR / run_id / "metrics.csv"
    assert len(archived.read_text().splitlines()) == 6           # архів оновлено


def test_continue_validation(client, demo_creature):
    assert client.post("/api/simulation/continue",
                       json={"run_id": "nope__20260101-0000"}).status_code == 404
    assert client.post("/api/simulation/continue", json={"run_id": "x", "extra": 1}).status_code == 422


def test_stopped_run_can_be_archived_by_hand(client, demo_creature):
    run_id = client.post("/api/simulation/start",
                         json=experiment(demo_creature, generations=500)).json()["run_id"]
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.post(f"/api/experiments/{run_id}/archive").status_code == 409   # ще виконується
    client.post("/api/simulation/stop")

    run = runs(client)[run_id]
    assert run["state"] == "STOPPED" and run["archived"] is False
    assert run["last_checkpoint"] == run["generations_done"]       # зупинка лишає чекпоінт

    r = client.post(f"/api/experiments/{run_id}/archive")
    assert r.status_code == 200 and r.json()["archived"] == f"{C.ARCHIVE_SUBDIR}/{run_id}"
    assert runs(client)[run_id]["archived"] is True
    assert client.post("/api/experiments/nope__20260101-0000/archive").status_code == 404


def test_hide_and_unhide_a_run(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    assert runs(client)[run_id]["hidden"] is False

    r = client.put(f"/api/experiments/{run_id}/hidden", json={"hidden": True})
    assert r.status_code == 200 and r.json() == {"run_id": run_id, "hidden": True}
    assert runs(client)[run_id]["hidden"] is True
    # Прогін не змінено: метрики й архів на місці
    assert (C.DATA_DIR / C.RUNS_SUBDIR / run_id / "metrics.csv").exists()
    assert (C.DATA_DIR / C.ARCHIVE_SUBDIR / run_id).exists()

    client.put(f"/api/experiments/{run_id}/hidden", json={"hidden": False})
    assert runs(client)[run_id]["hidden"] is False
    assert client.put("/api/experiments/nope__20260101-0000/hidden",
                      json={"hidden": True}).status_code == 404
    assert client.put(f"/api/experiments/{run_id}/hidden", json={}).status_code == 422


def test_broken_hidden_file_hides_nothing(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    (C.DATA_DIR / C.HIDDEN_RUNS_FILE).write_text("{обірвано")
    assert runs(client)[run_id]["hidden"] is False


def test_delete_an_accidental_run(client, demo_creature):
    run_id = client.post("/api/simulation/start",
                         json=experiment(demo_creature, generations=500)).json()["run_id"]
    deadline = time.monotonic() + 30
    while client.get("/api/simulation/status").json()["generation"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.delete(f"/api/experiments/{run_id}").status_code == 409     # ще виконується
    client.post("/api/simulation/stop")
    client.put(f"/api/experiments/{run_id}/hidden", json={"hidden": True})

    with client.websocket_connect("/ws/metrics") as ws:
        assert ws.receive_json()["run_id"] == run_id
        r = client.delete(f"/api/experiments/{run_id}")
        assert r.status_code == 200 and r.json() == {"deleted": run_id}
        assert ws.receive_json()["run_id"] is None                          # UI забуває прогін
    assert not (C.DATA_DIR / C.RUNS_SUBDIR / run_id).exists()
    assert run_id not in runs(client)
    assert client.get("/api/simulation/status").json()["run_id"] is None
    assert run_id not in (C.DATA_DIR / C.HIDDEN_RUNS_FILE).read_text()
    assert client.delete(f"/api/experiments/{run_id}").status_code == 404


def test_archived_run_cannot_be_deleted(client, demo_creature):
    run_id = finished_run(client, demo_creature)
    r = client.delete(f"/api/experiments/{run_id}")
    assert r.status_code == 409 and "архів" in r.json()["error"]["message"]
    assert (C.DATA_DIR / C.RUNS_SUBDIR / run_id).exists()
