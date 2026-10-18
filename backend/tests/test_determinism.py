"""Відтворюваність прогону (R7, §6.2): однаковий seed → побітово однаковий
`metrics.csv`, незалежно від кількості процесів оцінки."""

from __future__ import annotations

import numpy as np
import pytest

from app import config as C
from app.evolution.evaluator import Evaluator, episode_seed
from app.evolution.ga import GeneticAlgorithm, run_loop
from app.schemas import ExperimentConfig
from app.services.experiment_store import RunStore


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    return tmp_path


def small_cfg(demo_creature, seed: int = 11, name: str = "det") -> ExperimentConfig:
    # Короткі епізоди: перевіряємо відтворюваність, а не якість плавання
    return ExperimentConfig.model_validate({
        "name": name, "seed": seed, "creature": demo_creature,
        "environment": {"spawns": [{"position": [-5, 0], "angle": 0.0},
                                   {"position": [5, 3], "angle": 1.0}]},
        "ga": {"population": 8, "generations": 3, "episode_seconds": 1.0},
    })


def run_once(cfg: ExperimentConfig, workers: int) -> RunStore:
    store = RunStore.create(cfg)
    ga = GeneticAlgorithm(cfg)
    with Evaluator(cfg, workers) as evaluator:
        run_loop(ga, evaluator, store, until=cfg.ga.generations)
    return store


def test_same_seed_bit_identical_metrics(data_dir, demo_creature):
    cfg = small_cfg(demo_creature)
    a = run_once(cfg, workers=1)
    b = run_once(cfg, workers=1)
    assert a.run_id != b.run_id                     # новий прогін нічого не перезаписує
    metrics_a = (a.path / "metrics.csv").read_bytes()
    assert metrics_a == (b.path / "metrics.csv").read_bytes()
    assert metrics_a.count(b"\n") == 1 + cfg.ga.generations
    assert (a.path / "best_genome.json").read_bytes() == (b.path / "best_genome.json").read_bytes()


def test_worker_count_does_not_change_results(data_dir, demo_creature):
    """Пул із 2 процесів дає той самий файл, що й оцінка в одному процесі."""
    cfg = small_cfg(demo_creature)
    serial = run_once(cfg, workers=1)
    pooled = run_once(cfg, workers=2)
    assert (serial.path / "metrics.csv").read_bytes() == (pooled.path / "metrics.csv").read_bytes()


def test_different_seed_different_metrics(data_dir, demo_creature):
    a = run_once(small_cfg(demo_creature, seed=1), workers=1)
    b = run_once(small_cfg(demo_creature, seed=2), workers=1)
    assert (a.path / "metrics.csv").read_bytes() != (b.path / "metrics.csv").read_bytes()


def test_episode_seeds_follow_spawn_tree():
    """`spawn_key` дає рівно ті сіди, що й ланцюжок `SeedSequence.spawn` (§6.2)."""
    tree = np.random.SeedSequence(42).spawn(4)[3].spawn(6)[5].spawn(2)[1]
    assert episode_seed(42, 3, 5, 1) == int(tree.generate_state(1, dtype=np.uint64)[0])
    seeds = {episode_seed(42, g, i, e) for g in range(3) for i in range(5) for e in range(2)}
    assert len(seeds) == 30


def test_timing_kept_out_of_metrics(data_dir, demo_creature):
    """Час оцінки залежить від машини — він у `timing.csv`, а не в `metrics.csv` (§9.6)."""
    store = run_once(small_cfg(demo_creature), workers=1)
    header = (store.path / "metrics.csv").read_text().splitlines()[0]
    assert "eval_seconds" not in header and "run_id" not in header
    assert (store.path / "timing.csv").read_text().startswith("generation,eval_seconds")
    rows = store.read_generations()
    assert [r["generation"] for r in rows] == [1, 2, 3]
    assert all(r["eval_seconds"] > 0 for r in rows)


def test_archive_copies_light_files_once(data_dir, demo_creature):
    """§11: в архів — лише легкі файли, атомарно, без перезапису наявного."""
    store = run_once(small_cfg(demo_creature), workers=1)
    (store.path / "checkpoints").mkdir()
    (store.path / "checkpoints" / "gen_025.npz").write_bytes(b"x")
    target = store.archive()
    assert target == data_dir / C.ARCHIVE_SUBDIR / store.run_id
    assert sorted(p.name for p in target.iterdir()) == sorted(C.ARCHIVE_FILES)
    assert not list((data_dir / C.ARCHIVE_SUBDIR).glob(".*.tmp"))
    (target / "metrics.csv").write_text("правка руками")
    store.archive()
    assert (target / "metrics.csv").read_text() == "правка руками"
