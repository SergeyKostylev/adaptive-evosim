"""Хижак: кінематичне тіло та (з етапу 4) FSM PATROL / CHASE / RETURN (§8.1).

**Етап 2 реалізує лише тіло.** Хижак присутній у просторі фізично (істота може
в нього врізатись, промені радара його бачитимуть) і потрапляє в кадри §9.5
разом із радіусами, але стоїть на початку свого маршруту: виявлення, погоня та
повернення — зміст етапу 4. `state` поки що завжди `"PATROL"`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymunk

from .. import config as C
from ..schemas import EnvironmentConfig, PredatorConfig

PREDATOR_FILTER = pymunk.ShapeFilter(
    categories=C.CAT_PREDATOR,
    mask=C.CAT_CREATURE,        # з перешкодами хижак не взаємодіє (§8.2)
)


@dataclass
class Predator:
    """Хижак у сцені: тіло + його параметри + поточний стан FSM."""

    index: int
    cfg: PredatorConfig
    body: pymunk.Body
    shape: pymunk.Circle
    state: str = "PATROL"

    @property
    def position(self) -> pymunk.Vec2d:
        return self.body.position

    @property
    def r_catch_effective(self) -> float:
        """Дотик до тіла теж ловить, тож ефективний радіус — max(r_catch, r_body)."""
        return max(self.cfg.r_catch, self.cfg.r_body)

    def update(self, t: float, target: pymunk.Vec2d | None = None) -> None:
        """Етап 2: хижак нерухомий. FSM §8.1 приходить на етапі 4."""
        self.body.velocity = (0.0, 0.0)


def build_predators(space: pymunk.Space, env: EnvironmentConfig) -> list[Predator]:
    """Тіла всіх увімкнених хижаків сцени, кожен на початку свого маршруту."""
    out: list[Predator] = []
    for i, cfg in enumerate(env.predators):
        if not cfg.enabled or not cfg.waypoints:
            continue
        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        body.position = tuple(cfg.waypoints[0])
        shape = pymunk.Circle(body, cfg.r_body)
        shape.filter = PREDATOR_FILTER
        shape.collision_type = C.CAT_PREDATOR
        space.add(body, shape)
        out.append(Predator(index=i, cfg=cfg, body=body, shape=shape))
    return out
