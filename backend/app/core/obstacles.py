"""Статичні та кінематичні перешкоди + стіни світу (§8.2, §7.2).

Перешкоди взаємодіють лише з істотою: маска колізій виключає хижака (§8.2),
а всередині істоти колізії й так вимкнені групою.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pymunk

from .. import config as C
from ..schemas import (
    EnvironmentConfig,
    MovingPolygonObstacle,
    StaticCircleObstacle,
    StaticPolygonObstacle,
    WorldConfig,
)

OBSTACLE_FILTER = pymunk.ShapeFilter(
    categories=C.CAT_OBSTACLE,
    mask=C.CAT_CREATURE,        # з хижаком перешкоди не взаємодіють (§8.2)
)


@dataclass
class StaticObstacle:
    """Нерухома перешкода; у кадри не потрапляє — лише у пакет `scene` (§9.5)."""

    index: int
    kind: str
    shape: pymunk.Shape
    body: pymunk.Body
    #: Світові вершини (полігон) або `None` (коло)
    vertices: list[list[float]] | None
    position: tuple[float, float]
    radius: float | None


@dataclass
class MovingObstacle:
    """Полігон, що ходить між `p1` і `p2` синусоїдою (§8.2)."""

    index: int
    body: pymunk.Body
    shape: pymunk.Poly
    vertices: list[list[float]]
    p1: tuple[float, float]
    p2: tuple[float, float]
    period: float
    phase: float

    def pose_at(self, t: float) -> tuple[float, float]:
        """`pos(t) = lerp(p1, p2, 0.5 + 0.5·sin(2π t/period + phase))` (§8.2)."""
        s = 0.5 + 0.5 * math.sin(2 * math.pi * t / self.period + self.phase)
        return (self.p1[0] + (self.p2[0] - self.p1[0]) * s,
                self.p1[1] + (self.p2[1] - self.p1[1]) * s)

    def velocity_at(self, t: float) -> tuple[float, float]:
        """Похідна pose_at: кінематичному тілу потрібна швидкість, інакше
        зіткнення з ним виглядає як удар об нерухому стіну."""
        w = 2 * math.pi / self.period
        d = 0.5 * w * math.cos(w * t + self.phase)
        return ((self.p2[0] - self.p1[0]) * d, (self.p2[1] - self.p1[1]) * d)

    def update(self, t: float) -> None:
        self.body.position = self.pose_at(t)
        self.body.velocity = self.velocity_at(t)


def build_walls(space: pymunk.Space, world: WorldConfig) -> list[pymunk.Shape]:
    """Чотири стіни прямокутника `width × height` навколо центра координат.

    Осьова лінія стіни винесена назовні на товщину, тож внутрішня поверхня
    проходить рівно по межі світу.
    """
    w, h = world.width / 2, world.height / 2
    t = C.WALL_THICKNESS
    body = space.static_body
    spans = [
        ((-w - t, -h - t), (w + t, -h - t)),      # низ
        ((-w - t, h + t), (w + t, h + t)),        # верх
        ((-w - t, -h - t), (-w - t, h + t)),      # ліво
        ((w + t, -h - t), (w + t, h + t)),        # право
    ]
    shapes = []
    for a, b in spans:
        shape = pymunk.Segment(body, a, b, t)
        shape.filter = OBSTACLE_FILTER
        shape.collision_type = C.CAT_OBSTACLE
        space.add(shape)
        shapes.append(shape)
    return shapes


def world_vertices(vertices, position, angle: float = 0.0) -> list[list[float]]:
    """Локальні вершини → світові (дзеркало `worldVerts` у `shapes.js`)."""
    ca, sa = math.cos(angle), math.sin(angle)
    return [[position[0] + x * ca - y * sa, position[1] + x * sa + y * ca]
            for x, y in vertices]


def build_obstacles(
    space: pymunk.Space, env: EnvironmentConfig
) -> tuple[list[StaticObstacle], list[MovingObstacle]]:
    """Створює всі перешкоди сцени; індекси зберігаються для пакетів §9.5."""
    statics: list[StaticObstacle] = []
    movings: list[MovingObstacle] = []

    for i, obs in enumerate(env.obstacles):
        if isinstance(obs, StaticPolygonObstacle):
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
            body.position = tuple(obs.position)
            body.angle = obs.angle
            shape = pymunk.Poly(body, [tuple(v) for v in obs.vertices])
            shape.filter = OBSTACLE_FILTER
            shape.collision_type = C.CAT_OBSTACLE
            space.add(body, shape)
            statics.append(StaticObstacle(
                index=i, kind="static_polygon", shape=shape, body=body,
                vertices=world_vertices(obs.vertices, obs.position, obs.angle),
                position=tuple(obs.position), radius=None))

        elif isinstance(obs, StaticCircleObstacle):
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
            body.position = tuple(obs.position)
            shape = pymunk.Circle(body, obs.radius)
            shape.filter = OBSTACLE_FILTER
            shape.collision_type = C.CAT_OBSTACLE
            space.add(body, shape)
            statics.append(StaticObstacle(
                index=i, kind="static_circle", shape=shape, body=body,
                vertices=None, position=tuple(obs.position), radius=obs.radius))

        elif isinstance(obs, MovingPolygonObstacle):
            body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
            shape = pymunk.Poly(body, [tuple(v) for v in obs.vertices])
            shape.filter = OBSTACLE_FILTER
            shape.collision_type = C.CAT_OBSTACLE
            space.add(body, shape)
            moving = MovingObstacle(
                index=i, body=body, shape=shape,
                vertices=[list(v) for v in obs.vertices],
                p1=tuple(obs.p1), p2=tuple(obs.p2),
                period=obs.period, phase=obs.phase)
            moving.update(0.0)
            movings.append(moving)

    return statics, movings
