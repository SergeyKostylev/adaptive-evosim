"""Хижак (FSM §8.1), радар і аура (§4.1–4.2), спіймання й колізії в `World` (§7)."""

from __future__ import annotations

import math

import numpy as np
import pymunk
import pytest

from app import config as C
from app.core.creature import build_creature
from app.core.physics import PhysicsWorld
from app.core.predator import build_predators, route_length, route_point
from app.core.sensors import Sensors
from app.core.world import World
from app.schemas import CreatureConfig, EnvironmentConfig

SQUARE = [[-5, -5], [5, -5], [5, 5], [-5, 5]]


def env(**kw) -> EnvironmentConfig:
    return EnvironmentConfig.model_validate(kw)


def one_predator(waypoints, rng=None, **params):
    space = pymunk.Space()
    (p,) = build_predators(space, env(predators=[{"waypoints": waypoints, **params}]), rng)
    return space, p


def advance(space, p, target, seconds):
    for _ in range(round(seconds / C.DT)):
        p.update(target)
        space.step(C.DT)


# --- FSM ---------------------------------------------------------------------
def test_patrol_visits_waypoints_in_order_and_loops():
    space, p = one_predator(SQUARE)
    assert tuple(p.position) == (-5, -5) and p.target == 1
    visited = []
    for _ in range(round(2 * route_length(p.waypoints) / C.V_PATROL / C.DT)):
        before = p.target
        p.update(None)
        space.step(C.DT)
        if p.target != before:
            visited.append(before)
            assert p.position.get_distance(p.waypoints[before]) < 1e-6   # кут не проскочено
    assert visited[:5] == [1, 2, 3, 0, 1]
    assert p.state == "PATROL"


def test_detect_chase_then_lose_by_distance_and_return():
    space, p = one_predator(SQUARE)
    advance(space, p, (-5 + C.R_DETECT - 1, -5), C.DT)
    assert p.state == "CHASE"
    speed = p.body.velocity.length
    assert speed == pytest.approx(C.V_CHASE)
    # Істота «телепортувалась» далеко — далі за 1.5·R_detect → RETURN
    advance(space, p, (100, 100), C.DT)
    assert p.state == "RETURN"
    advance(space, p, None, 10.0)
    assert p.state == "PATROL"


def test_chase_gives_up_without_progress():
    """Ціль тікає з тією самою швидкістю: дистанція не скорочується → RETURN за T_giveup."""
    space, p = one_predator(SQUARE, v_chase=1.0)
    target = [-5 + 4.0, -5.0]
    advance(space, p, tuple(target), C.DT)
    assert p.state == "CHASE"
    steps = 0
    while p.state == "CHASE":
        target[0] += 1.0 * C.DT * 1.01          # трохи швидше за хижака
        advance(space, p, tuple(target), C.DT)
        steps += 1
        assert steps * C.DT < C.T_GIVEUP + 1
    assert p.state == "RETURN"
    assert steps * C.DT == pytest.approx(C.T_GIVEUP, abs=0.05)


def test_start_phase_comes_from_episode_rng():
    starts = []
    for seed in (1, 2, 1):
        _, p = one_predator(SQUARE, np.random.Generator(np.random.PCG64(seed)))
        starts.append(tuple(p.position))
        on_edge = any(abs(abs(c) - 5) < 1e-9 for c in starts[-1])
        assert on_edge and all(abs(c) <= 5 + 1e-9 for c in starts[-1])
    assert starts[0] == starts[2] != starts[1]


def test_route_point_walks_the_perimeter():
    wps = [(0.0, 0.0), (4.0, 0.0)]                 # два waypoint-и — туди й назад
    assert route_length(wps) == 8.0
    assert route_point(wps, 1.0) == ((1.0, 0.0), 1)
    assert route_point(wps, 6.0) == ((2.0, 0.0), 0)


# --- сенсори -------------------------------------------------------------------
@pytest.fixture
def creature_cfg(demo_creature) -> CreatureConfig:
    return CreatureConfig.model_validate(demo_creature)


def sensors_in(environment, creature_cfg, angle=0.0):
    physics = PhysicsWorld(environment)
    creature = build_creature(creature_cfg, (0.0, 0.0), angle)
    physics.add_creature(creature)
    return Sensors(creature, physics.space, physics.predators), physics


BIG = {"world": {"width": 200, "height": 200}}       # стіни далі за R_ray


def test_rays_ignore_own_shapes(creature_cfg):
    """Промені стартують з-під власного тіла й ланок — і не бачать їх (§4.1)."""
    sensors, _ = sensors_in(env(**BIG), creature_cfg)
    x = sensors.read(0.0)
    assert np.all(x[:C.N_RAYS * C.N_RAY_CHANNELS] == 0.0)
    assert all(hit is None and frac == 1.0 for frac, hit in sensors.rays)


def test_ray_sees_obstacle_and_predator_on_separate_channels(creature_cfg):
    environment = env(**BIG, obstacles=[{"type": "static_circle", "radius": 1.0, "position": [6, 0]}],
                      predators=[{"waypoints": [[0, 8], [0, 9]], "r_body": 1.0}])
    sensors, _ = sensors_in(environment, creature_cfg)
    x = sensors.read(0.0)
    # Промінь 0 — уперед (+x): перешкода, поверхня на відстані 5
    assert x[0] == pytest.approx(1 - 5 / C.R_RAY) and x[1] == 0.0
    # Промінь 3 — ліворуч (+y, 90°): хижак, поверхня на відстані 7
    assert x[6] == 0.0 and x[7] == pytest.approx(1 - 7 / C.R_RAY)
    assert sensors.rays[0][1] == "obstacle" and sensors.rays[3][1] == "predator"


def test_rays_turn_with_the_body(creature_cfg):
    environment = env(**BIG, obstacles=[{"type": "static_circle", "radius": 1.0, "position": [6, 0]}])
    sensors, _ = sensors_in(environment, creature_cfg, angle=math.pi / 2)
    x = sensors.read(0.0)
    # Тіло дивиться на +y, перешкода праворуч — промінь 9 (270°)
    assert x[2 * 9] == pytest.approx(1 - 5 / C.R_RAY)
    assert x[0] == 0.0


def test_walls_are_obstacles_for_radar(creature_cfg):
    sensors, _ = sensors_in(env(world={"width": 10, "height": 40}), creature_cfg)
    x = sensors.read(0.0)
    assert x[0] == pytest.approx(1 - 5 / C.R_RAY)      # стіна x = 5, промінь уперед


def test_aura_bearing_in_local_frame(creature_cfg):
    k = C.N_RAYS * C.N_RAY_CHANNELS
    r_sense = C.R_AURA_SENSE_FACTOR * C.R_AURA
    environment = env(**BIG, predators=[{"waypoints": [[0, 3], [0, 4]]}])
    sensors, _ = sensors_in(environment, creature_cfg)
    a, a_sin, a_cos = sensors.read(0.0)[k:k + 3]
    assert a == pytest.approx(1 - 3 / r_sense)
    assert sensors.bearing == pytest.approx(math.pi / 2)      # ліворуч
    assert (a_sin, a_cos) == pytest.approx((a, 0.0), abs=1e-12)
    # Та сама сцена, тіло повернуте на 90°: хижак тепер прямо попереду
    sensors, _ = sensors_in(environment, creature_cfg, angle=math.pi / 2)
    sensors.read(0.0)
    assert sensors.bearing == pytest.approx(0.0, abs=1e-12)


def test_aura_out_of_range_is_zero(creature_cfg):
    k = C.N_RAYS * C.N_RAY_CHANNELS
    environment = env(**BIG, predators=[{"waypoints": [[0, 50], [0, 51]]}])
    sensors, _ = sensors_in(environment, creature_cfg)
    assert np.all(sensors.read(0.0)[k:k + 3] == 0.0)


# --- World: спіймання, колізії ---------------------------------------------------
def test_catch_ends_episode_and_costs_survival(creature_cfg):
    environment = env(predators=[{"waypoints": [[3, 0], [4, 0]]}])
    world = World(creature_cfg, environment, seed=5, episode_seconds=10.0)
    world.run()
    assert world.ended_reason == "caught" and not world.alive
    assert world.t < 3.0
    _, comps = world.fitness_result()
    assert comps["S"] == pytest.approx(world.t / 10.0)
    assert comps["G_aura"] > 0
    assert world.snapshot()["hud"]["ended"] == "caught"


def test_without_predators_episode_runs_to_the_end(creature_cfg):
    world = World(creature_cfg, env(), seed=5, episode_seconds=1.0)
    world.run()
    assert world.ended_reason == "time"
    _, comps = world.fitness_result()
    assert comps["S"] == 1.0 and comps["G_aura"] == 0.0


def test_obstacle_contact_counts_collision(creature_cfg):
    """Перешкода впритул до тіла: перший же крок — контакт → `C > 0`."""
    environment = env(obstacles=[{"type": "static_circle", "radius": 1.0, "position": [1.7, 0]}])
    world = World(creature_cfg, environment, seed=1, episode_seconds=1.0)
    world.run()
    _, comps = world.fitness_result()
    assert comps["C"] >= 1 / C.N_HITS_NORM


def test_snapshot_carries_rays_and_predator_state(creature_cfg):
    environment = env(predators=[{"waypoints": [[6, 0], [7, 0]]}])
    world = World(creature_cfg, environment, seed=2, episode_seconds=5.0)
    world.step()
    frame = world.snapshot()
    assert len(frame["sensors"]["rays"]) == C.N_RAYS
    assert frame["predators"][0]["state"] == "CHASE"
    assert frame["sensors"]["aura"] > 0
