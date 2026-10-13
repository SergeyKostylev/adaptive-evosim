"""Побудова Pymunk-тіл, суглобів і моторів із `CreatureConfig` (§3).

Один суглоб на сегмент: тіло↔сегмент₀, далі сегментᵢ₋₁↔сегментᵢ. Кожен суглоб —
трійка `PivotJoint` (точка кріплення) + `RotaryLimitJoint` (ліміти відносно
нейтралі) + `SimpleMotor` (керований `rate`, §3).

Колізії всередині істоти вимкнені спільною `ShapeFilter(group=...)`, тож
сегменти вільно перетинаються між собою й із тілом.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pymunk

from .. import config as C
from ..schemas import BodyConfig, CreatureConfig
from .hydro import sample_offsets

#: Групи колізій нумеруються з 1: 0 у Chipmunk означає «групи немає».
_group_counter = itertools.count(1)

#: Знак, яким команда мережі перетворюється на `motor.rate`. Chipmunk жене
#: `b.w − a.w + rate` до нуля, тобто додатний `rate` зменшує відносний кут;
#: щоб `u > 0` збільшувало θ_j, команду інвертуємо.
MOTOR_RATE_SIGN = -1.0


def polygon_area(vertices: list[list[float]]) -> float:
    """Модуль площі простого полігона (формула шнурків)."""
    v = np.asarray(vertices, dtype=np.float64)
    x, y = v[:, 0], v[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2.0


def body_perimeter_point(body: BodyConfig, angle: float) -> tuple[float, float]:
    """Точка кріплення на периметрі тіла під кутом `angle` (дзеркало `shapes.js`).

    Для полігона — найдальший перетин променя з центра зі сторонами: це робить
    кріплення коректним і для невипуклих на вигляд, витягнутих форм.
    """
    dx, dy = math.cos(angle), math.sin(angle)
    if body.shape == "circle":
        return body.radius * dx, body.radius * dy

    best = 0.0
    verts = body.vertices or []
    for i in range(len(verts)):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % len(verts)]
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = (x1 * ey - y1 * ex) / den            # відстань уздовж променя
        u = (dx * y1 - dy * x1) / -den           # параметр на стороні [0, 1]
        if t > 0 and -1e-9 <= u <= 1 + 1e-9:
            best = max(best, t)
    return best * dx, best * dy


@dataclass
class Segment:
    """Одна ланка кінцівки разом зі своїм суглобом."""

    body: pymunk.Body
    shape: pymunk.Poly
    parent: pymunk.Body
    motor: pymunk.SimpleMotor
    limit: pymunk.RotaryLimitJoint
    pivot: pymunk.PivotJoint
    limb: int
    idx: int
    joint_index: int
    length: float
    width: float
    #: Нейтральний **відносний** кут `b.angle − a.angle`: ліміти й θ_j рахуються від нього
    rest_relative: float
    #: Зсуви точок вибірки гідросил уздовж осі сегмента (§2.2)
    sample_offsets: np.ndarray

    @property
    def angle(self) -> float:
        return float(self.body.angle)

    @property
    def joint_angle(self) -> float:
        """θ_j — кут суглоба відносно нейтралі, зведений до (−π, π]."""
        raw = self.body.angle - self.parent.angle - self.rest_relative
        return float((raw + math.pi) % (2 * math.pi) - math.pi)

    @property
    def joint_rate(self) -> float:
        """ω_j — відносна кутова швидкість ланки щодо батьківської."""
        return float(self.body.angular_velocity - self.parent.angular_velocity)

    def sample_points(self) -> np.ndarray:
        """Світові координати точок вибірки, `(N_s, 2)`."""
        ca, sa = math.cos(self.body.angle), math.sin(self.body.angle)
        px, py = self.body.position
        return np.stack([px + self.sample_offsets * ca,
                         py + self.sample_offsets * sa], axis=1)


@dataclass
class Creature:
    """Зібрана істота: центральне тіло + ланцюжки сегментів."""

    body: pymunk.Body
    shape: pymunk.Shape
    segments: list[Segment]
    group: int
    radius: float                    # еквівалентний радіус для опору тіла (§2.2)
    config: CreatureConfig
    constraints: list[pymunk.Constraint] = field(default_factory=list)

    @property
    def n_joints(self) -> int:
        return len(self.segments)

    @property
    def bodies(self) -> list[pymunk.Body]:
        return [self.body] + [s.body for s in self.segments]

    @property
    def shapes(self) -> list[pymunk.Shape]:
        return [self.shape] + [s.shape for s in self.segments]

    @property
    def position(self) -> pymunk.Vec2d:
        return self.body.position

    def joint_angles(self) -> np.ndarray:
        return np.array([s.joint_angle for s in self.segments], dtype=np.float64)

    def joint_rates(self) -> np.ndarray:
        return np.array([s.joint_rate for s in self.segments], dtype=np.float64)

    def set_motor_rates(self, commands: np.ndarray) -> None:
        """Команди мережі `u ∈ [−1, 1]` → `motor.rate = u·OMEGA_MAX` (§3).

        Значення тримається між control-тиками: мотор не скидається щокроку.
        """
        u = np.clip(np.asarray(commands, dtype=np.float64), -1.0, 1.0)
        for seg, value in zip(self.segments, u):
            seg.motor.rate = MOTOR_RATE_SIGN * float(value) * C.OMEGA_MAX

    def add_to(self, space: pymunk.Space) -> None:
        space.add(self.body, self.shape)
        for seg in self.segments:
            space.add(seg.body, seg.shape)
        space.add(*self.constraints)


def _make_body_shape(cfg: BodyConfig) -> tuple[pymunk.Body, pymunk.Shape, float]:
    """Центральне тіло: маса й момент із густини та геометрії (§2.3)."""
    if cfg.shape == "circle":
        radius = float(cfg.radius)
        mass = max(cfg.density * math.pi * radius**2, C.MIN_SEGMENT_MASS)
        body = pymunk.Body(mass, pymunk.moment_for_circle(mass, 0, radius))
        return body, pymunk.Circle(body, radius), radius

    verts = [tuple(v) for v in cfg.vertices]
    mass = max(cfg.density * polygon_area(cfg.vertices), C.MIN_SEGMENT_MASS)
    body = pymunk.Body(mass, pymunk.moment_for_poly(mass, verts))
    # Еквівалентний радіус для ізотропного опору — круг тієї самої площі
    radius = math.sqrt(polygon_area(cfg.vertices) / math.pi)
    return body, pymunk.Poly(body, verts), radius


def build_creature(
    cfg: CreatureConfig,
    position: tuple[float, float] = (0.0, 0.0),
    angle: float = 0.0,
) -> Creature:
    """Збирає істоту в заданій позі світу.

    Усе спершу рахується в локальній системі істоти (як у редакторі), потім
    повертається й зсувається — так геометрія збігається з тим, що намальовано
    на канвасі, незалежно від місця появи.
    """
    group = next(_group_counter)
    shape_filter = pymunk.ShapeFilter(
        group=group,
        categories=C.CAT_CREATURE,
        mask=C.CAT_OBSTACLE | C.CAT_PREDATOR,
    )
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def to_world(lx: float, ly: float) -> tuple[float, float]:
        return (position[0] + lx * cos_a - ly * sin_a,
                position[1] + lx * sin_a + ly * cos_a)

    central, central_shape, radius = _make_body_shape(cfg.body)
    central.position = position
    central.angle = angle
    central_shape.filter = shape_filter
    central_shape.collision_type = C.CAT_CREATURE

    segments: list[Segment] = []
    constraints: list[pymunk.Constraint] = []
    joint_index = 0

    for limb_i, limb in enumerate(cfg.limbs):
        ax, ay = body_perimeter_point(cfg.body, limb.attach_angle)
        direction = limb.attach_angle + limb.rest_angle
        dx, dy = math.cos(direction), math.sin(direction)
        parent: pymunk.Body = central
        reach = 0.0

        for seg_i, seg_cfg in enumerate(limb.segments):
            mass = max(seg_cfg.density * seg_cfg.length * seg_cfg.width, C.MIN_SEGMENT_MASS)
            moment = pymunk.moment_for_box(mass, (seg_cfg.length, seg_cfg.width))
            seg_body = pymunk.Body(mass, moment)
            seg_body.position = to_world(ax + dx * (reach + seg_cfg.length / 2),
                                         ay + dy * (reach + seg_cfg.length / 2))
            seg_body.angle = angle + direction

            shape = pymunk.Poly.create_box(seg_body, (seg_cfg.length, seg_cfg.width))
            shape.filter = shape_filter
            shape.collision_type = C.CAT_CREATURE

            # Суглоб — у проксимальному кінці ланки (кінці попередньої)
            pivot_world = to_world(ax + dx * reach, ay + dy * reach)
            pivot = pymunk.PivotJoint(parent, seg_body, pivot_world)
            # Нейтральний відносний кут: для першої ланки це напрямок кінцівки,
            # далі ланки продовжують одна одну, тож нейтраль — нуль
            rest_relative = direction if seg_i == 0 else 0.0
            limit = pymunk.RotaryLimitJoint(
                parent, seg_body,
                rest_relative + seg_cfg.joint.angle_min,
                rest_relative + seg_cfg.joint.angle_max,
            )
            motor = pymunk.SimpleMotor(parent, seg_body, 0.0)
            motor.max_force = seg_cfg.joint.motor_torque
            constraints.extend((pivot, limit, motor))

            segments.append(Segment(
                body=seg_body, shape=shape, parent=parent,
                motor=motor, limit=limit, pivot=pivot,
                limb=limb_i, idx=seg_i, joint_index=joint_index,
                length=seg_cfg.length, width=seg_cfg.width,
                rest_relative=rest_relative,
                sample_offsets=sample_offsets(seg_cfg.length),
            ))
            joint_index += 1
            reach += seg_cfg.length
            parent = seg_body

    return Creature(
        body=central, shape=central_shape, segments=segments,
        group=group, radius=radius, config=cfg, constraints=constraints,
    )
