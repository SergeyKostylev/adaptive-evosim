"""Побудова істоти з `CreatureConfig` у Pymunk-тіла та суглоби (§3).

Перевіряється те, що легко зламати непомітно: геометрія кріплення, нейтраль
суглобів, знак керування мотором, фільтри колізій і маси.
"""

from __future__ import annotations

import math

import numpy as np
import pymunk
import pytest

from app import config as C
from app.core.creature import (
    MOTOR_RATE_SIGN,
    body_perimeter_point,
    build_creature,
    polygon_area,
)
from app.core.physics import PhysicsWorld
from app.core.world import World
from app.schemas import CreatureConfig, EnvironmentConfig


def make_cfg(limbs=(2.3562, -2.3562), segments=((1.2, 0.18), (0.9, 0.12)), body=None) -> CreatureConfig:
    body = body or {"shape": "circle", "radius": 0.8, "vertices": None, "density": 1.0}
    return CreatureConfig.model_validate({
        "name": "swimmer-v1",
        "body": body,
        "limbs": [{
            "attach_angle": a, "rest_angle": 0.0,
            "segments": [{
                "length": length, "width": width, "density": 1.0,
                "joint": {"angle_min": -1.2, "angle_max": 1.2, "motor_torque": 120.0},
            } for length, width in segments],
        } for a in limbs],
    })


# --- геометрія -------------------------------------------------------------
def test_perimeter_point_of_circle():
    body = make_cfg().body
    for angle in (0.0, 1.0, -2.5, math.pi):
        x, y = body_perimeter_point(body, angle)
        assert math.hypot(x, y) == pytest.approx(0.8)
        assert math.atan2(y, x) == pytest.approx(math.atan2(math.sin(angle), math.cos(angle)))


def test_perimeter_point_of_polygon_lies_on_edge():
    cfg = make_cfg(body={"shape": "polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
                         "radius": None, "density": 1.0})
    x, y = body_perimeter_point(cfg.body, 0.0)
    assert (x, y) == pytest.approx((1.0, 0.0))
    x, y = body_perimeter_point(cfg.body, math.pi / 2)
    assert (x, y) == pytest.approx((0.0, 1.0))


def test_polygon_area_is_orientation_independent():
    square = [[0, 0], [2, 0], [2, 2], [0, 2]]
    assert polygon_area(square) == pytest.approx(4.0)
    assert polygon_area(list(reversed(square))) == pytest.approx(4.0)


def test_joint_count_matches_schema():
    cfg = make_cfg()
    creature = build_creature(cfg)
    assert creature.n_joints == cfg.n_joints == 4
    assert len(creature.bodies) == 5           # тіло + 4 сегменти


def test_segments_are_chained_from_attachment_point():
    """Сегменти продовжують один одного від точки на периметрі (§3)."""
    cfg = make_cfg(limbs=(0.0,), segments=((1.2, 0.18), (0.9, 0.12)))
    creature = build_creature(cfg)
    first, second = creature.segments
    # Центр першого — на 0.8 + 1.2/2 від центра тіла вздовж +X
    assert first.body.position.x == pytest.approx(0.8 + 0.6)
    assert first.body.position.y == pytest.approx(0.0, abs=1e-9)
    # Другий починається там, де закінчився перший
    assert second.body.position.x == pytest.approx(0.8 + 1.2 + 0.45)


def test_spawn_pose_rotates_and_translates_whole_creature():
    cfg = make_cfg(limbs=(0.0,), segments=((1.2, 0.18),))
    at_origin = build_creature(cfg)
    moved = build_creature(cfg, position=(5.0, -3.0), angle=math.pi / 2)
    assert moved.body.position == pytest.approx((5.0, -3.0))
    assert moved.body.angle == pytest.approx(math.pi / 2)
    # Кінцівка, що дивилась уздовж +X, тепер дивиться вздовж +Y
    local = at_origin.segments[0].body.position
    assert moved.segments[0].body.position.x == pytest.approx(5.0, abs=1e-9)
    assert moved.segments[0].body.position.y == pytest.approx(-3.0 + local.x)


def test_sample_points_span_the_segment():
    creature = build_creature(make_cfg(limbs=(0.0,), segments=((1.2, 0.18),)))
    seg = creature.segments[0]
    points = seg.sample_points()
    assert points.shape == (C.N_S, 2)
    # Центри третин навколо центра мас сегмента
    centre = np.array(seg.body.position)
    offsets = points - centre
    assert np.linalg.norm(offsets, axis=1) == pytest.approx([0.4, 0.0, 0.4])


# --- маси та інерція -------------------------------------------------------
def test_masses_are_positive_and_respect_minimum():
    creature = build_creature(make_cfg(segments=((0.3, 0.05),)))
    assert creature.body.mass > 0 and creature.body.moment > 0
    for seg in creature.segments:
        assert seg.body.mass >= C.MIN_SEGMENT_MASS
        assert seg.body.moment > 0


def test_polygon_body_mass_follows_area_and_density():
    cfg = make_cfg(body={"shape": "polygon", "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
                         "radius": None, "density": 2.0})
    creature = build_creature(cfg)
    assert creature.body.mass == pytest.approx(2.0 * 4.0)
    # Еквівалентний радіус — круг тієї самої площі
    assert creature.radius == pytest.approx(math.sqrt(4.0 / math.pi))


# --- суглоби ---------------------------------------------------------------
def test_joint_limits_are_relative_to_rest_angle():
    """Ліміти задані відносно нейтралі, тож зміщені на нейтральний кут (§3)."""
    cfg = make_cfg(limbs=(1.0,), segments=((1.2, 0.18), (0.9, 0.12)))
    creature = build_creature(cfg)
    first, second = creature.segments
    assert first.rest_relative == pytest.approx(1.0)       # attach + rest
    assert first.limit.min == pytest.approx(1.0 - 1.2)
    assert first.limit.max == pytest.approx(1.0 + 1.2)
    assert second.rest_relative == pytest.approx(0.0)      # ланка продовжує попередню
    assert second.limit.min == pytest.approx(-1.2)


def test_neutral_pose_has_zero_joint_angles():
    creature = build_creature(make_cfg(limbs=(1.3, -0.4)))
    assert creature.joint_angles() == pytest.approx(np.zeros(creature.n_joints), abs=1e-9)
    assert creature.joint_rates() == pytest.approx(np.zeros(creature.n_joints))


def test_motor_torque_becomes_max_force():
    creature = build_creature(make_cfg())
    assert all(seg.motor.max_force == 120.0 for seg in creature.segments)


def test_motor_rate_scales_with_omega_max():
    creature = build_creature(make_cfg(limbs=(0.0,), segments=((1.2, 0.18),)))
    creature.set_motor_rates([1.0])
    assert creature.segments[0].motor.rate == pytest.approx(MOTOR_RATE_SIGN * C.OMEGA_MAX)
    # Команда поза [−1, 1] обрізається, а не підсилює мотор
    creature.set_motor_rates([5.0])
    assert abs(creature.segments[0].motor.rate) == pytest.approx(C.OMEGA_MAX)


def test_positive_command_increases_joint_angle_and_limits_hold():
    """Знак керування: `u > 0` збільшує θ_j, ліміти не пускають далі (§3)."""
    creature = build_creature(make_cfg(limbs=(0.0,), segments=((1.2, 0.18),)))
    space = pymunk.Space()
    space.gravity = (0, 0)
    creature.add_to(space)
    creature.body.body_type = pymunk.Body.STATIC        # ізолюємо суглоб від руху корпусу

    creature.set_motor_rates([1.0])
    for _ in range(240):
        space.step(C.DT)
    assert creature.joint_angles()[0] > 1.0
    assert creature.joint_angles()[0] <= 1.2 + 0.05      # м'який ліміт трохи пружинить

    creature.set_motor_rates([-1.0])
    for _ in range(240):
        space.step(C.DT)
    assert creature.joint_angles()[0] < -1.0


# --- колізії ---------------------------------------------------------------
def test_all_shapes_share_one_group_so_limbs_do_not_collide():
    creature = build_creature(make_cfg())
    groups = {shape.filter.group for shape in creature.shapes}
    assert len(groups) == 1 and groups != {0}
    for shape in creature.shapes:
        assert shape.filter.categories == C.CAT_CREATURE
        assert shape.filter.mask == C.CAT_OBSTACLE | C.CAT_PREDATOR


def test_two_creatures_get_different_groups():
    a = build_creature(make_cfg())
    b = build_creature(make_cfg())
    assert a.group != b.group


def test_creature_collides_with_obstacles_but_not_with_itself():
    env = EnvironmentConfig.model_validate({
        "obstacles": [{"type": "static_circle", "radius": 1.5, "position": [3.0, 0.0]}]})
    world = PhysicsWorld(env)
    creature = build_creature(make_cfg())
    world.add_creature(creature)
    own = creature.shapes[0]
    obstacle = world.static_obstacles[0].shape
    predator_like = pymunk.ShapeFilter(categories=C.CAT_PREDATOR, mask=C.CAT_CREATURE)
    # Спільна група вимикає колізії всередині істоти (§3)
    assert pymunk.ShapeFilter.rejects_collision(own.filter, creature.shapes[1].filter)
    # Але з перешкодами та хижаком — ні
    assert not pymunk.ShapeFilter.rejects_collision(own.filter, obstacle.filter)
    assert not pymunk.ShapeFilter.rejects_collision(own.filter, predator_like)
    # А перешкода з хижаком не взаємодіє (§8.2)
    assert pymunk.ShapeFilter.rejects_collision(obstacle.filter, predator_like)


# --- інтеграція з епізодом -------------------------------------------------
def test_demo_creature_swims_on_sine(demo_creature):
    """DoD етапу 2: синусоїда дає ≥3 од. чистого зміщення за 30 сим-с."""
    cfg = CreatureConfig.model_validate(demo_creature)
    world = World(cfg, EnvironmentConfig(), episode_seconds=30.0)
    world.run()
    assert world.steps == 3600
    assert world.displacement >= 3.0
    assert world.is_finite()
    assert world.max_speed <= C.V_MAX


def test_episode_is_deterministic(demo_creature):
    """Той самий конфіг і сід → побітово той самий кінцевий стан."""
    cfg = CreatureConfig.model_validate(demo_creature)

    def final_state():
        world = World(cfg, EnvironmentConfig(), seed=7, episode_seconds=5.0)
        world.run()
        return [(b.position.x, b.position.y, b.angle) for b in world.creature.bodies]

    assert final_state() == final_state()


def test_velocity_clamp_holds_for_absurd_motor_commands(demo_creature):
    """Погані команди не мають розганяти тіла понад V_MAX (§2.3)."""
    cfg = CreatureConfig.model_validate(demo_creature)

    class Insane:
        def __call__(self, t, world):
            return np.full(world.n_joints, 1.0 if int(t * 20) % 2 else -1.0)

    world = World(cfg, EnvironmentConfig(), episode_seconds=10.0, controller=Insane())
    while world.alive:
        world.step()
        assert world.max_speed <= C.V_MAX + 1e-9
    assert world.is_finite()
