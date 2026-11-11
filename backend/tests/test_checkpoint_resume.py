"""Чекпоінти й продовження прогону (§11, DoD етапу 5).

Головне: продовжений прогін дає **побітово той самий** `metrics.csv` і
найкращий геном, що й неперервний — і після аварії, і після зупинки, і при
подовженні. Плюс аварійне вбивство справжнього процесу (`kill -9`): метрики
цілі, останній чекпоінт читається.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from app import config as C
from app.evolution.evaluator import Evaluator
from app.evolution.ga import GeneticAlgorithm, resume, run_loop
from app.schemas import ExperimentConfig
from app.services.experiment_store import RunStore, list_runs

BACKEND = Path(__file__).resolve().parents[1]


class Crash(Exception):
    """Імітація аварії: процес «помирає» після запису рядка покоління."""


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "GA_CHECKPOINT_EVERY", 2)
    return tmp_path


def small_cfg(demo_creature, generations: int = 6, name: str = "ckpt") -> ExperimentConfig:
    # Хижак біля старту — щоб випадкова фаза патруля й спіймання теж були
    # під побітовим порівнянням
    return ExperimentConfig.model_validate({
        "name": name, "seed": 5, "creature": demo_creature,
        "environment": {"spawns": [{"position": [-5, 0], "angle": 0.0}],
                        "predators": [{"waypoints": [[-8, 0], [0, 4], [8, 3]]}]},
        "ga": {"population": 6, "generations": generations, "episode_seconds": 1.0},
    })


def run_full(cfg: ExperimentConfig) -> RunStore:
    store = RunStore.create(cfg)
    with Evaluator(cfg, 1) as ev:
        run_loop(GeneticAlgorithm(cfg), ev, store, until=cfg.ga.generations)
    return store


def continue_to_end(store: RunStore, generations: int | None = None) -> None:
    cfg, ga, _ = resume(store, generations)
    with Evaluator(cfg, 1) as ev:
        run_loop(ga, ev, store, until=cfg.ga.generations)


def same_results(a: RunStore, b: RunStore) -> None:
    assert (a.path / "metrics.csv").read_bytes() == (b.path / "metrics.csv").read_bytes()
    ga, gb = a.load_best_genome(), b.load_best_genome()
    assert ga["weights"] == gb["weights"]


def test_checkpoints_every_n_and_at_the_end(data_dir, demo_creature):
    store = run_full(small_cfg(demo_creature, generations=5))
    assert store.checkpoints() == [2, 4, 5]
    state = store.load_checkpoint()
    assert state["generation"] == 5
    assert state["population"].shape == (6, small_cfg(demo_creature).creature.genome_size)
    assert state["population"].dtype == np.float32


def test_resume_after_crash_is_bit_identical(data_dir, demo_creature):
    cfg = small_cfg(demo_creature)
    reference = run_full(cfg)

    store = RunStore.create(cfg)

    def die(stats):
        if stats["generation"] == 5:
            raise Crash
    with Evaluator(cfg, 1) as ev, pytest.raises(Crash):
        run_loop(GeneticAlgorithm(cfg), ev, store, until=cfg.ga.generations, on_generation=die)
    assert store.checkpoints() == [2, 4]           # покоління 5 записане, чекпоінта ще нема

    continue_to_end(store)
    same_results(reference, store)


def test_resume_after_stop_is_bit_identical(data_dir, demo_creature):
    cfg = small_cfg(demo_creature)
    reference = run_full(cfg)

    store = RunStore.create(cfg)
    done = []
    with Evaluator(cfg, 1) as ev:
        run_loop(GeneticAlgorithm(cfg), ev, store, until=cfg.ga.generations,
                 before_generation=lambda: len(done) < 3, on_generation=done.append)
    assert store.checkpoints() == [2, 3]           # зупинка теж лишає чекпоінт

    continue_to_end(store)
    same_results(reference, store)


def test_extending_a_finished_run_matches_a_longer_one(data_dir, demo_creature):
    reference = run_full(small_cfg(demo_creature, generations=6))
    store = run_full(small_cfg(demo_creature, generations=4))
    continue_to_end(store, generations=6)
    same_results(reference, store)
    assert store.load_config().ga.generations == 6


def test_resume_refuses_what_it_cannot_do(data_dir, demo_creature):
    store = run_full(small_cfg(demo_creature, generations=4))
    with pytest.raises(ValueError, match="дійшов"):
        resume(store)
    with pytest.raises(ValueError, match="менше"):
        resume(store, generations=3)

    empty = RunStore.create(small_cfg(demo_creature))
    with pytest.raises(ValueError, match="чекпоінт"):
        resume(empty)


def test_truncate_drops_later_rows_and_a_torn_line(data_dir, demo_creature):
    store = run_full(small_cfg(demo_creature, generations=4))
    metrics = store.path / "metrics.csv"
    with metrics.open("a", encoding="utf-8") as fh:
        fh.write("5,1.2345")                       # обірваний рядок після kill -9
    store.truncate_to(2)
    lines = metrics.read_text(encoding="utf-8").splitlines()
    assert [ln.split(",")[0] for ln in lines] == ["generation", "1", "2"]
    assert len((store.path / "timing.csv").read_text().splitlines()) == 3


def test_archive_replace_only_when_asked(data_dir, demo_creature):
    store = run_full(small_cfg(demo_creature, generations=4))
    target = store.archive()
    rows = (target / "metrics.csv").read_text()
    continue_to_end(store, generations=6)
    assert (store.archive() / "metrics.csv").read_text() == rows          # не перезаписано
    assert (store.archive(replace=True) / "metrics.csv").read_text() != rows
    assert not list(target.parent.glob(".*"))      # без тимчасових каталогів


def test_list_runs_merges_runs_and_archive(data_dir, demo_creature):
    store = run_full(small_cfg(demo_creature, generations=2, name="only-archive"))
    store.archive()
    for p in store.path.rglob("*"):
        if p.is_file():
            p.unlink()
    for p in sorted(store.path.rglob("*"), reverse=True):
        p.rmdir()
    store.path.rmdir()
    local = run_full(small_cfg(demo_creature, generations=2, name="local"))

    runs = {st.run_id: st for st in list_runs()}
    assert runs[store.run_id].location == "archive"
    assert runs[local.run_id].location == "runs"
    summary = runs[store.run_id].summary()
    assert summary["generations_done"] == 2 and summary["last_checkpoint"] is None
    assert summary["archived"] is True
    assert RunStore.open(store.run_id).location == "archive"


def test_kill_9_leaves_readable_metrics_and_checkpoint(tmp_path, demo_creature, monkeypatch):
    """Справжній процес, справжній SIGKILL посеред прогону (DoD етапу 5)."""
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    cfg = small_cfg(demo_creature, generations=1000, name="kill")
    cfg = cfg.model_copy(update={"ga": cfg.ga.model_copy(update={"episode_seconds": 0.25})})
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(cfg.model_dump_json())
    env = {**os.environ, "EVOSIM_DATA_DIR": str(tmp_path), "PYTHONPATH": str(BACKEND)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.evolution.ga", "--config", str(cfg_path), "--workers", "1"],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 120
        first = C.GA_CHECKPOINT_EVERY
        while time.monotonic() < deadline:
            runs = list((tmp_path / C.RUNS_SUBDIR).glob("kill__*"))
            if runs and (runs[0] / C.CHECKPOINTS_SUBDIR / f"gen_{first:03d}.npz").exists():
                break
            assert proc.poll() is None, proc.stderr.read().decode()
            time.sleep(0.05)
        else:
            pytest.fail("чекпоінт так і не з'явився")
        time.sleep(0.3)                            # ще кілька поколінь після чекпоінта
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=30)
    assert proc.returncode == -signal.SIGKILL

    store = RunStore.open(runs[0].name)
    rows = store.read_generations()                # парситься без помилок
    assert [r["generation"] for r in rows[:first]] == list(range(1, first + 1))
    state = store.load_checkpoint()
    assert state["generation"] >= first
    assert json.dumps(state["rng_state"])          # стан генератора цілий

    # Продовження з нього до найближчого покоління після чекпоінта дає ті самі
    # рядки, що записав убитий процес
    killed = [ln for ln in (store.path / "metrics.csv").read_text().splitlines(keepends=True)
              if ln.endswith("\n")]               # обірваний хвіст не рахується
    cfg2, ga, _ = resume(store)
    stop_at = state["generation"] + 2
    with Evaluator(cfg2, 1) as ev:
        run_loop(ga, ev, store, until=stop_at)
    resumed = (store.path / "metrics.csv").read_text().splitlines(keepends=True)
    assert len(resumed) == stop_at + 1
    common = min(len(killed), len(resumed))
    assert resumed[:common] == killed[:common]


def test_champion_is_reselected_on_shared_test_scenarios(data_dir, demo_creature):
    from app.export import champion

    store = run_full(small_cfg(demo_creature, generations=3))
    report = champion.select(store, episodes=6, workers=1)
    assert report == champion.select(store, episodes=6, workers=1)          # відтворювано
    assert report["checkpoint_generation"] == 3
    # Рекорд — теж кандидат, тож чемпіон на тестових сценаріях не гірший за нього
    assert report["champion"]["mean_F"] >= report["record"]["mean_F"]
    tops = [r["mean_F"] for r in report["top10"]]
    assert tops == sorted(tops, reverse=True) and tops[0] == report["champion"]["mean_F"]
    assert report["champion"]["episodes"] == 6
    paths = champion.write(store, report)
    assert paths == [str(store.path / "champion.json")]

    empty = RunStore.create(small_cfg(demo_creature))
    with pytest.raises(ValueError, match="чекпоінт"):
        champion.select(empty)
