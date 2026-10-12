"""Схеми §9.1–9.3: похідні величини, хеш морфології, сцена Save→Load."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app import config as C
from app.schemas import (
    CreatureConfig,
    EnvironmentConfig,
    ExperimentConfig,
    GenomeJSON,
    is_convex,
)

SCENE = {
    "world": {"width": 60, "height": 40},
    "fluid": {"rho": 1.0, "c_d_perp": 2.0, "c_d_par": 0.1, "c_l": 0.5, "linear_damping": 0.98},
    "predator": {
        "enabled": True,
        "waypoints": [[-15, -10], [15, -10], [15, 10], [-15, 10]],
        "v_patrol": 2.0, "v_chase": 3.5,
        "r_detect": 8.0, "r_aura": 5.0, "r_catch": 1.0,
    },
    "obstacles": [
        {"type": "static_polygon", "vertices": [[0, 0], [3, 0], [3, 1], [0, 1]],
         "position": [5, 5], "angle": 0.3},
        {"type": "static_circle", "radius": 1.5, "position": [-8, 2]},
        {"type": "moving_polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
         "p1": [0, -10], "p2": [0, 10], "period": 12.0, "phase": 0.0},
    ],
}


def test_spec_scene_roundtrip():
    """Save → Load: серіалізація сцени відтворюється побітово (DoD етапу 1)."""
    env = EnvironmentConfig.model_validate(SCENE)
    again = EnvironmentConfig.model_validate(env.model_dump(mode="json"))
    assert again == env
    assert [o.type for o in env.obstacles] == ["static_polygon", "static_circle", "moving_polygon"]


def test_n_joints_counts_every_segment(demo_creature):
    creature = CreatureConfig.model_validate(demo_creature)
    assert creature.n_joints == 4
    assert creature.n_in == C.n_in_from_joints(4) == 46
    assert creature.genome_size == C.genome_size(46, 4)


def test_creature_hash_ignores_name_but_tracks_morphology(demo_creature):
    base = CreatureConfig.model_validate(demo_creature)
    renamed = CreatureConfig.model_validate({**demo_creature, "name": "інша-назва"})
    assert base.creature_hash == renamed.creature_hash

    changed = copy.deepcopy(demo_creature)
    changed["limbs"][0]["segments"][0]["length"] += 0.1
    assert CreatureConfig.model_validate(changed).creature_hash != base.creature_hash


def test_genome_json_size_must_match_morphology(demo_creature):
    creature = CreatureConfig.model_validate(demo_creature)
    ok = GenomeJSON(
        creature_hash=creature.creature_hash,
        n_in=creature.n_in,
        n_joints=creature.n_joints,
        weights=[0.0] * creature.genome_size,
    )
    assert len(ok.weights) == creature.genome_size
    with pytest.raises(ValidationError):
        GenomeJSON(
            creature_hash=creature.creature_hash,
            n_in=creature.n_in,
            n_joints=creature.n_joints,
            weights=[0.0] * (creature.genome_size - 1),
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"shape": "circle", "radius": 0.8, "vertices": [[0, 0], [1, 0], [0, 1]]},  # обидві форми
        {"shape": "circle", "radius": 0.1},                                        # < 0.3
        {"shape": "polygon", "vertices": [[0, 0], [1, 0]]},                        # < 3 вершин
        {"shape": "polygon", "radius": 0.8, "vertices": [[0, 0], [1, 0], [0, 1]]},  # radius ≠ null
    ],
)
def test_body_shape_rules(demo_creature, patch):
    bad = copy.deepcopy(demo_creature)
    bad["body"] = {"density": 1.0, **patch}
    with pytest.raises(ValidationError):
        CreatureConfig.model_validate(bad)


def test_limits_from_spec(demo_creature):
    too_many_segments = copy.deepcopy(demo_creature)
    too_many_segments["limbs"][0]["segments"] *= 3            # 6 > MAX_SEGMENTS
    with pytest.raises(ValidationError):
        CreatureConfig.model_validate(too_many_segments)

    bad_joint = copy.deepcopy(demo_creature)
    bad_joint["limbs"][0]["segments"][0]["joint"]["angle_min"] = -3.0   # < -2.5
    with pytest.raises(ValidationError):
        CreatureConfig.model_validate(bad_joint)

    bad_torque = copy.deepcopy(demo_creature)
    bad_torque["limbs"][0]["segments"][0]["joint"]["motor_torque"] = 5.0  # < 10
    with pytest.raises(ValidationError):
        CreatureConfig.model_validate(bad_torque)


def test_predator_radii_order():
    bad = copy.deepcopy(SCENE)
    bad["predator"]["r_aura"] = 10.0        # > r_detect
    with pytest.raises(ValidationError):
        EnvironmentConfig.model_validate(bad)


def test_experiment_config_defaults(demo_creature):
    exp = ExperimentConfig.model_validate({"name": "exp-baseline-01", "creature": demo_creature})
    assert exp.seed == 42
    assert exp.ga.population == C.GA_POPULATION
    assert exp.ga.episode_seconds == C.GA_EPISODE_SECONDS
    assert exp.fitness_weights.survival == C.W_SURVIVAL
    assert exp.environment.world.width == C.W_WORLD


def test_is_convex():
    assert is_convex([[0, 0], [2, 0], [2, 2], [0, 2]])
    assert is_convex([[0, 2], [2, 2], [2, 0], [0, 0]])              # CW теж
    assert not is_convex([[0, 0], [2, 0], [1, 1], [2, 2], [0, 2]])   # увігнутий
    assert not is_convex([[0, 0], [1, 0], [2, 0]])                   # колінеарні


# ---------------------------------------------------------------------------
# Сцена: позиції істот та кілька хижаків (§9.2)
# ---------------------------------------------------------------------------
def test_spawns_and_multiple_predators():
    """Точка появи — лише позиція: морфологію задає ExperimentConfig.creature."""
    env = EnvironmentConfig.model_validate({
        "spawns": [
            {"position": [-20, 0]},
            {"position": [12, -6], "angle": 1.57, "label": "старт-B"},
        ],
        "predators": [
            {"waypoints": [[-15, -10], [15, -10]]},
            {"preset": "швидкий", "waypoints": [[-5, 5], [5, 5]],
             "r_aura": 7.0, "r_detect": 9.0, "enabled": False},
        ],
    })
    assert [s.position for s in env.spawns] == [[-20.0, 0.0], [12.0, -6.0]]
    assert env.spawns[1].angle == 1.57 and env.spawns[1].label == "старт-B"
    assert env.predators[1].preset == "швидкий"
    with pytest.raises(ValidationError):            # прив'язка істоти прибрана
        EnvironmentConfig.model_validate({"spawns": [{"creature": "x", "position": [0, 0]}]})
    assert len(env.predators) == 2
    assert [p.r_aura for p in env.predators] == [5.0, 7.0]
    assert len(env.enabled_predators) == 1          # другий вимкнений


def test_legacy_single_predator_is_folded():
    """Сцени за старим контрактом (§9.2 до розширення) лишаються читабельними."""
    env = EnvironmentConfig.model_validate({
        "world": {"width": 60, "height": 40},
        "predator": {"enabled": True, "waypoints": [[-15, -10], [15, -10]]},
        "obstacles": [],
    })
    assert len(env.predators) == 1
    assert env.predators[0].waypoints == [[-15.0, -10.0], [15.0, -10.0]]

    dumped = env.model_dump(mode="json")
    assert "predator" not in dumped                 # legacy-поле не серіалізується
    assert EnvironmentConfig.model_validate(dumped) == env


def test_explicit_predators_win_over_legacy_field():
    env = EnvironmentConfig.model_validate({
        "predator": {"waypoints": [[0, 0], [1, 1]]},
        "predators": [{"waypoints": [[2, 2], [3, 3]]}, {"waypoints": [[4, 4], [5, 5]]}],
    })
    assert len(env.predators) == 2
    assert env.predators[0].waypoints == [[2.0, 2.0], [3.0, 3.0]]


def test_scene_limits():
    with pytest.raises(ValidationError):            # > 16 позицій
        EnvironmentConfig.model_validate(
            {"spawns": [{"position": [0, 0]} for _ in range(17)]})
    with pytest.raises(ValidationError):            # > 8 хижаків
        EnvironmentConfig.model_validate(
            {"predators": [{"waypoints": [[0, 0], [1, 1]]} for _ in range(9)]})
    with pytest.raises(ValidationError):            # маршрут < 2 точок
        EnvironmentConfig.model_validate({"predators": [{"waypoints": [[0, 0]]}]})


def test_experiment_config_accepts_scene(demo_creature):
    exp = ExperimentConfig.model_validate({
        "name": "exp-scene-01",
        "creature": demo_creature,
        "environment": {
            "spawns": [{"position": [0, 0], "angle": 0.5}],
            "predators": [{"waypoints": [[-15, -10], [15, -10]]}],
        },
    })
    assert exp.environment.spawns[0].angle == 0.5
    assert len(exp.environment.predators) == 1


def test_predator_body_radius():
    """§8.1: розмір хижака тепер налаштовний, дефолт — 0.8."""
    env = EnvironmentConfig.model_validate({"predators": [
        {"waypoints": [[0, 0], [1, 1]]},
        {"waypoints": [[0, 0], [1, 1]], "r_body": 1.6},
    ]})
    assert env.predators[0].r_body == C.R_PREDATOR_BODY == 0.8
    assert env.predators[1].r_body == 1.6

    for bad in (0.1, 3.5):
        with pytest.raises(ValidationError):
            EnvironmentConfig.model_validate(
                {"predators": [{"waypoints": [[0, 0], [1, 1]], "r_body": bad}]})
