"""Володіє `pymunk.Space`: крок `DT`, гідросили по точках вибірки, клампи (§2.1, §2.3).

Порядок кроку жорстко заданий специфікацією і є частиною відтворюваності:
гідросили рахуються зі швидкостей **на початку** кроку, застосовуються,
і лише потім `space.step(DT)`; після кроку — жорсткий кламп `|v| ≤ V_MAX`.

Колізії істоти з перешкодами й хижаками простір лише **повідомляє**
(`on_obstacle_contact`, `predator_touched`): що з ними робити — справа фітнесу
й `World` (§7.1, §7.2).
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pymunk

from .. import config as C
from ..schemas import EnvironmentConfig
from . import hydro
from .creature import Creature
from .obstacles import MovingObstacle, StaticObstacle, build_obstacles, build_walls
from .predator import Predator, build_predators


class PhysicsWorld:
    """Простір одного епізоду: стіни, перешкоди, хижаки та істоти в рідині."""

    def __init__(self, env: EnvironmentConfig, rng: np.random.Generator | None = None):
        self.env = env
        self.fluid = env.fluid
        self.space = pymunk.Space()
        self.space.gravity = C.GRAVITY          # R1: нейтральна плавучість, фіксовано
        # Семантика Pymunk — частка швидкості, що лишається за секунду (§2.1)
        self.space.damping = self.fluid.linear_damping

        self.walls: list[pymunk.Shape] = build_walls(self.space, env.world)
        self.static_obstacles: list[StaticObstacle]
        self.moving_obstacles: list[MovingObstacle]
        self.static_obstacles, self.moving_obstacles = build_obstacles(self.space, env)
        self.predators: list[Predator] = build_predators(self.space, env, rng)
        self.creatures: list[Creature] = []
        self._layouts: list[_SamplingLayout] = []
        self._clamped: list[list[pymunk.Body]] = []

        #: Викликається з шейпом перешкоди на кожен початок контакту з істотою
        self.on_obstacle_contact: Callable[[pymunk.Shape], None] | None = None
        #: Чи торкнулась будь-яка ланка істоти тіла хижака (§7.2)
        self.predator_touched = False
        self.space.on_collision(C.CAT_CREATURE, C.CAT_OBSTACLE, begin=self._obstacle_begin)
        self.space.on_collision(C.CAT_CREATURE, C.CAT_PREDATOR, begin=self._predator_begin)

    # --- колізії -----------------------------------------------------------
    def _obstacle_begin(self, arbiter: pymunk.Arbiter, space, data) -> None:
        if self.on_obstacle_contact is not None:
            self.on_obstacle_contact(arbiter.shapes[1])

    def _predator_begin(self, arbiter: pymunk.Arbiter, space, data) -> None:
        self.predator_touched = True

    # --- склад сцени -------------------------------------------------------
    def add_creature(self, creature: Creature) -> None:
        creature.add_to(self.space)
        self.creatures.append(creature)
        self._layouts.append(_SamplingLayout(creature))
        self._clamped.append(creature.bodies)

    # --- крок --------------------------------------------------------------
    def apply_fluid_forces(self, creature: Creature, layout: "_SamplingLayout") -> None:
        """Гідросили на всі ланки істоти (§2.2). Швидкості — **до** кроку.

        На кожен сегмент кладеться рівнодійна точкових сил і її момент
        (`hydro.segment_resultants`): це тотожно прикладанню кожної сили у своїй
        точці, але замість `N_s` переходів у C маємо два на сегмент.
        """
        f = self.fluid
        segments = creature.segments
        if segments:
            states = layout.states
            for i, seg in enumerate(segments):
                body = seg.body
                vx, vy = body.velocity
                states[i] = (body.angle, vx, vy, body.angular_velocity,
                             states[i][4], states[i][5])
            resultants = hydro.segment_resultants(
                states, rho=f.rho, c_d_perp=f.c_d_perp,
                c_d_par=f.c_d_par, c_l=f.c_l,
            )
            # `space.step` обнуляє force/torque після кроку, а інших джерел сил
            # у сегментів немає (мотори діють імпульсами) — тож присвоєння
            for seg, (fx, fy, torque) in zip(segments, resultants):
                seg.body.force = (fx, fy)
                seg.body.torque = torque

        # Кругле тіло: ізотропний опір + обертальне демпфування диска (§2.2)
        body = creature.body
        vx, vy = body.velocity
        speed = math.hypot(vx, vy)
        if speed > hydro.EPS:
            k = -0.5 * f.rho * C.C_D_BODY * (2.0 * creature.radius) * speed
            fx, fy = k * vx, k * vy
            mag = math.hypot(fx, fy)
            if mag > C.F_MAX:
                scale = C.F_MAX / mag
                fx, fy = fx * scale, fy * scale
            body.force = (fx, fy)
        omega = body.angular_velocity
        body.torque = -f.rho * C.C_D_BODY * creature.radius**4 * abs(omega) * omega

    def clamp_velocities(self) -> None:
        """Жорстке обрізання `|v| ≤ V_MAX` — захист від вибуху (§2.3).

        Порівняння у квадратах: нормальний крок не має платити за `sqrt` і
        створення `Vec2d` там, де кламп майже ніколи не спрацьовує.
        """
        limit_sq = C.V_MAX * C.V_MAX
        for bodies in self._clamped:
            for body in bodies:
                vx, vy = body.velocity
                speed_sq = vx * vx + vy * vy
                if speed_sq > limit_sq:
                    scale = C.V_MAX / math.sqrt(speed_sq)
                    body.velocity = (vx * scale, vy * scale)

    def step(self, t: float) -> None:
        """Один крок `DT` у строгому порядку §2.3."""
        for obstacle in self.moving_obstacles:
            obstacle.update(t)
        target = self.creatures[0].body.position if self.creatures else None
        for predator in self.predators:
            predator.update(target)
        for creature, layout in zip(self.creatures, self._layouts):
            self.apply_fluid_forces(creature, layout)
        self.space.step(C.DT)
        self.clamp_velocities()


class _SamplingLayout:
    """Передобчислена розкладка точок вибірки однієї істоти.

    Геометрія сегментів за епізод не змінюється, тож зсуви та `L_k` рахуються
    один раз; на кожному кроці оновлюються лише кінематичні поля кортежів —
    на гарячому шляху жодних алокацій під фіксовані дані.
    """

    def __init__(self, creature: Creature):
        #: `(angle, vx, vy, omega, offsets, length_k)` для `hydro.segment_resultants`
        self.states: list[tuple[float, float, float, float, tuple[float, ...], float]] = [
            (0.0, 0.0, 0.0, 0.0, tuple(float(o) for o in seg.sample_offsets),
             seg.length / C.N_S)
            for seg in creature.segments
        ]
