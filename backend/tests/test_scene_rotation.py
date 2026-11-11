"""Поворот сцени на епізод (§6.2, `ga.scene_rotation`).

Головне: поворот — жорстке перетворення всієї сцени (відстані зберігаються),
кут береться з окремого потоку й генератор епізоду не чіпає (фази патруля ті
самі), прогін відтворюваний, а вимкнений прапорець нічого не змінює.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from app import config as C
from app.core.world import World, rotate_environment, scene_angle
from app.evolution import genome as G
from app.evolution.evaluator import Evaluator
from app.evolution.ga import GeneticAlgorithm, run_loop
from app.schemas import CreatureConfig, EnvironmentConfig, ExperimentConfig
from app.services.experiment_store import RunStore

SCENE = {
    "spawns": [{"position": [2, 1], "angle": 0.3}, {"position": [-3, 0], "angle": 2.0}],
    "predators": [{"waypoints": [[0, 4], [0, 15]]}, {"waypoints": [[-3.5, -2], [-14, -8]]}],
    "obstacles": [
        {"type": "static_circle", "radius": 1.0, "position": [5, -5]},
        {"type": "static_polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
         "position": [-6, 6], "angle": 0.2},
        {"type": "moving_polygon", "vertices": [[-0.5, -0.5], [0.5, -0.5], [0, 0.6]],
         "p1": [8, 0], "p2": [8, 6], "period": 4.0},
    ],
}


def norm(v):
    return math.hypot(v[0], v[1])


def test_rotation_is_rigid_and_zero_is_identity():
    env = EnvironmentConfig.model_validate(SCENE)
    assert rotate_environment(env, 0.0).model_dump() == env.model_dump()

    theta = 1.1
    rot = rotate_environment(env, theta)
    for a, b in zip(env.predators, rot.predators):
        assert [norm(w) for w in a.waypoints] == pytest.approx([norm(w) for w in b.waypoints])
    for a, b in zip(env.spawns, rot.spawns):
        assert norm(b.position) == pytest.approx(norm(a.position))
        assert b.angle == pytest.approx(a.angle + theta)
    w = env.predators[0].waypoints[1]
    assert rot.predators[0].waypoints[1] == pytest.approx(
        [w[0] * math.cos(theta) - w[1] * math.sin(theta), w[0] * math.sin(theta) + w[1] * math.cos(theta)])
    poly = rot.obstacles[1]
    assert poly.angle == pytest.approx(0.2 + theta) and poly.vertices == env.obstacles[1].vertices
    moving = rot.obstacles[2]
    assert norm(moving.p2) == pytest.approx(norm(env.obstacles[2].p2))
    assert rot.world == env.world                      # стіни не повертаються


def test_angle_is_deterministic_and_spread():
    angles = [scene_angle(s) for s in range(400)]
    assert angles == [scene_angle(s) for s in range(400)]
    assert all(0 <= a < 2 * math.pi for a in angles)
    hist, _ = np.histogram(angles, bins=4, range=(0, 2 * math.pi))
    assert hist.min() > 60                             # рівномірно, без перекосу


def test_rotation_keeps_patrol_phases(demo_creature):
    """Кут — з окремого потоку: хижаки стартують у тих самих точках маршрутів,
    лише повернутих разом зі сценою."""
    creature = CreatureConfig.model_validate(demo_creature)
    env = EnvironmentConfig.model_validate(SCENE)
    for seed in (1, 7, 42):
        plain = World(creature, env, seed=seed)
        turned = World(creature, env, seed=seed, rotate_scene=True)
        theta = turned.scene_rotation
        assert theta == scene_angle(seed)
        c, s = math.cos(theta), math.sin(theta)
        for p, q in zip(plain.predators, turned.predators):
            x, y = p.body.position
            assert tuple(q.body.position) == pytest.approx((c * x - s * y, s * x + c * y), abs=1e-9)
        assert norm(turned.start_position) == pytest.approx(norm(plain.start_position))


def test_off_by_default_and_old_configs_unchanged(demo_creature):
    cfg = ExperimentConfig.model_validate({"name": "x", "creature": demo_creature})
    assert cfg.ga.scene_rotation is False
    world = World(cfg.creature, EnvironmentConfig.model_validate(SCENE), seed=3)
    assert world.scene_rotation == 0.0


def test_scene_must_fit_any_angle(demo_creature):
    ok = {"name": "x", "creature": demo_creature, "environment": SCENE,
          "ga": {"scene_rotation": True}}
    ExperimentConfig.model_validate(ok)                # найдальша точка — 15 < 20
    far = {**ok, "environment": {**SCENE, "predators": [{"waypoints": [[0, 4], [25, 0]]}]}}
    with pytest.raises(ValidationError, match="поворот сцени"):
        ExperimentConfig.model_validate(far)
    # Без повороту та сама сцена — законна
    ExperimentConfig.model_validate({**far, "ga": {"scene_rotation": False}})


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    return tmp_path


def rot_cfg(demo_creature, rotation=True, shared=True) -> ExperimentConfig:
    return ExperimentConfig.model_validate({
        "name": "rot", "seed": 9, "creature": demo_creature, "environment": SCENE,
        "ga": {"population": 6, "generations": 3, "episode_seconds": 1.5,
               "shared_scenarios": shared, "scene_rotation": rotation},
    })


def run(cfg) -> bytes:
    store = RunStore.create(cfg)
    with Evaluator(cfg, 1) as ev:
        run_loop(GeneticAlgorithm(cfg), ev, store, until=cfg.ga.generations)
    return (store.path / "metrics.csv").read_bytes()


def test_rotated_run_is_reproducible_and_differs(data_dir, demo_creature):
    a = run(rot_cfg(demo_creature))
    assert a == run(rot_cfg(demo_creature))
    assert a != run(rot_cfg(demo_creature, rotation=False))


def test_shared_scenarios_share_the_angle(demo_creature):
    """Зі спільними сценаріями однакові геноми в одному поколінні — однаковий фітнес."""
    cfg = rot_cfg(demo_creature)
    rng = np.random.Generator(np.random.PCG64(0))
    one = G.init_population(1, cfg.creature.n_in, cfg.creature.n_joints, rng)[0]
    pop = np.stack([one] * 4)
    with Evaluator(cfg, 1) as ev:
        res = ev.evaluate(pop, gen=2)
    assert np.all(res.episode_fitness == res.episode_fitness[0])
