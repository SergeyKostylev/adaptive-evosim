"""Хижак: кінематичне тіло та FSM PATROL / CHASE / RETURN (§8.1).

Рух — лише через `body.velocity`: Pymunk сам інтегрує позицію кінематичного
тіла в `space.step`, гідросили на нього не діють. FSM оновлюється перед кожним
фізичним кроком за позицією істоти на його початку.

Стартова точка на маршруті — з генератора епізоду (§7.3): різні епізоди однієї
особини зустрічають хижака в різний час.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pymunk

from .. import config as C
from ..schemas import EnvironmentConfig, PredatorConfig

PREDATOR_FILTER = pymunk.ShapeFilter(
    categories=C.CAT_PREDATOR,
    mask=C.CAT_CREATURE,        # з перешкодами хижак не взаємодіє (§8.2)
)


def route_point(waypoints: list[tuple[float, float]], s: float) -> tuple[tuple[float, float], int]:
    """Точка замкненого маршруту на відстані `s` уздовж периметра від першого
    waypoint-а і індекс waypoint-а, до якого звідти йти (§7.3, §8.1)."""
    n = len(waypoints)
    for i in range(n):
        a, b = waypoints[i], waypoints[(i + 1) % n]
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if s < seg:
            k = s / seg
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k), (i + 1) % n
        s -= seg
    return waypoints[0], 1 % n


def route_length(waypoints: list[tuple[float, float]]) -> float:
    n = len(waypoints)
    return sum(math.hypot(waypoints[(i + 1) % n][0] - waypoints[i][0],
                          waypoints[(i + 1) % n][1] - waypoints[i][1]) for i in range(n))


@dataclass
class Predator:
    """Хижак у сцені: тіло + його параметри + поточний стан FSM."""

    index: int
    cfg: PredatorConfig
    body: pymunk.Body
    shape: pymunk.Circle
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    state: str = "PATROL"
    #: Індекс waypoint-а, до якого хижак зараз іде (PATROL і RETURN)
    target: int = 0
    #: CHASE: найменша досягнута відстань і час без її скорочення
    best_dist: float = math.inf
    stall: float = 0.0

    @property
    def position(self) -> pymunk.Vec2d:
        return self.body.position

    @property
    def r_catch_effective(self) -> float:
        """Дотик до тіла теж ловить, тож ефективний радіус — max(r_catch, r_body)."""
        return max(self.cfg.r_catch, self.cfg.r_body)

    # --- рух ----------------------------------------------------------------
    def _set_velocity(self, vx: float, vy: float) -> None:
        self.body.velocity = (vx, vy)
        if vx or vy:
            self.body.angle = math.atan2(vy, vx)

    def _go_to_waypoint(self, speed: float) -> bool:
        """Крок до поточного waypoint-а; `True`, якщо на цьому кроці його досягнуто.
        Близький waypoint береться рівно, без проскакування кута (§8.1)."""
        px, py = self.body.position
        wx, wy = self.waypoints[self.target]
        dx, dy = wx - px, wy - py
        dist = math.hypot(dx, dy)
        if dist <= speed * C.DT:
            self._set_velocity(dx / C.DT, dy / C.DT)
            return True
        self._set_velocity(speed * dx / dist, speed * dy / dist)
        return False

    def _nearest_waypoint(self) -> int:
        px, py = self.body.position
        return min(range(len(self.waypoints)),
                   key=lambda i: math.hypot(self.waypoints[i][0] - px, self.waypoints[i][1] - py))

    # --- FSM (§8.1) ---------------------------------------------------------
    def update(self, target: tuple[float, float] | None) -> None:
        """Один крок FSM перед `space.step`. `target` — центр тіла істоти
        (`None` — істоти немає, хижак просто патрулює)."""
        cfg = self.cfg
        px, py = self.body.position
        dist = math.inf
        if target is not None:
            dist = math.hypot(target[0] - px, target[1] - py)

        if self.state == "PATROL" and dist < cfg.r_detect:
            self.state = "CHASE"
            self.best_dist = dist
            self.stall = 0.0
        elif self.state == "CHASE":
            if dist < self.best_dist:
                self.best_dist = dist
                self.stall = 0.0
            else:
                self.stall += C.DT
            if dist > C.CHASE_EXIT_FACTOR * cfg.r_detect or self.stall > C.T_GIVEUP:
                self.state = "RETURN"
                self.target = self._nearest_waypoint()

        if self.state == "CHASE":
            dx, dy = target[0] - px, target[1] - py
            if dist > 1e-9:
                self._set_velocity(cfg.v_chase * dx / dist, cfg.v_chase * dy / dist)
            else:
                self._set_velocity(0.0, 0.0)
        elif self._go_to_waypoint(cfg.v_patrol):
            self.target = (self.target + 1) % len(self.waypoints)
            self.state = "PATROL"


def build_predators(space: pymunk.Space, env: EnvironmentConfig,
                    rng: np.random.Generator | None = None) -> list[Predator]:
    """Тіла всіх увімкнених хижаків сцени.

    З генератором епізоду кожен стартує у випадковій точці свого маршруту (§7.3),
    по одному числу на хижака в порядку `predators`; без нього — на першому waypoint-і.
    """
    out: list[Predator] = []
    for i, cfg in enumerate(env.predators):
        if not cfg.enabled or not cfg.waypoints:
            continue
        waypoints = [(float(x), float(y)) for x, y in cfg.waypoints]
        if rng is not None:
            start, target = route_point(waypoints, float(rng.random()) * route_length(waypoints))
        else:
            start, target = waypoints[0], 1 % len(waypoints)
        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        body.position = start
        nx, ny = waypoints[target]
        if (nx, ny) != start:
            body.angle = math.atan2(ny - start[1], nx - start[0])
        shape = pymunk.Circle(body, cfg.r_body)
        shape.filter = PREDATOR_FILTER
        shape.collision_type = C.CAT_PREDATOR
        space.add(body, shape)
        out.append(Predator(index=i, cfg=cfg, body=body, shape=shape,
                            waypoints=waypoints, target=target))
    return out
