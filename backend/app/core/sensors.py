"""Вектор входів мережі: radar-raycast, аура-сенсор, пропріоцепція,
осцилятор (§4). Пропріоцепція+осцилятор — етап 3, радар і аура — етап 4.

Промені бачать лише перешкоди (зокрема стіни) і хижаків: фільтр запиту має
маску `CAT_OBSTACLE | CAT_PREDATOR` і групу істоти, тож власні шейпи, з
середини яких промінь стартує, відкидаються (§4.1). Аура відчувається крізь
перешкоди — це відстань, а не промінь (§4.2).

Усе — у локальній системі істоти, щоб поведінка не залежала від того, де у
світі й під яким кутом вона опинилась. Порядок груп у векторі фіксований
(радар, аура, суглоби, тіло, осцилятор); вимкнена група випадає (§4.4).
Вектор читається лише на control-тиках — мозку між ними він не потрібен.
"""

from __future__ import annotations

import math

import numpy as np
import pymunk

from .. import config as C
from .creature import Creature
from .predator import Predator

#: Підписи влучань для кадру §9.5, за `collision_type` шейпа
HIT_KINDS = {C.CAT_OBSTACLE: "obstacle", C.CAT_PREDATOR: "predator"}


def _side_scale(limit: float) -> float:
    """`1/|limit|` для нормалізації θ; межа нульової ширини дає 0 (нейтраль)."""
    return 1.0 / abs(limit) if abs(limit) > 1e-9 else 0.0


class Sensors:
    """Збирає вектор входів довжини `N_in` для однієї істоти."""

    def __init__(self, creature: Creature, space: pymunk.Space | None = None,
                 predators: list[Predator] | None = None):
        if C.SENSE_RADAR and space is None:
            raise ValueError("радару потрібен простір Pymunk")
        self.creature = creature
        self.space = space
        self.predators = predators or []
        self.n_in = C.n_in_from_joints(creature.n_joints)
        self._filter = pymunk.ShapeFilter(
            group=creature.group, categories=C.CAT_CREATURE,
            mask=C.CAT_OBSTACLE | C.CAT_PREDATOR)
        #: Напрямки променів у системі тіла: k = 0 — уперед (+x), далі проти годинникової
        self._ray_dirs = [(math.cos(2 * math.pi * k / C.N_RAYS),
                           math.sin(2 * math.pi * k / C.N_RAYS)) for k in range(C.N_RAYS)]
        #: Останнє зчитування для кадру §9.5: `(frac, hit)` на промінь, аура, пеленг
        self.rays: list[tuple[float, str | None]] = [(1.0, None)] * C.N_RAYS
        self.aura = 0.0
        self.bearing = 0.0
        # Ліміти суглобів з конфігу: θ̂ = θ/angle_max (θ ≥ 0) або θ/|angle_min| (§4.3)
        self._scales = []
        for limb in creature.config.limbs:
            for seg in limb.segments:
                self._scales.append((_side_scale(seg.joint.angle_max),
                                     _side_scale(seg.joint.angle_min)))
        self._out = np.zeros(self.n_in, dtype=np.float64)

    def read(self, t: float) -> np.ndarray:
        """Вектор входів у момент `t`. Повертає **той самий** буфер щоразу."""
        out = self._out
        k = 0
        half_pi = math.pi / 2
        inv_omega = 1.0 / C.OMEGA_MAX
        body = self.creature.body
        cx, cy = body.position
        phi = body.angle
        cos_p, sin_p = math.cos(phi), math.sin(phi)

        # Радар: [d_obs, d_pred] на промінь (§4.1)
        if C.SENSE_RADAR:
            query = self.space.segment_query_first
            rays = self.rays
            for i, (lx, ly) in enumerate(self._ray_dirs):
                dx = lx * cos_p - ly * sin_p
                dy = lx * sin_p + ly * cos_p
                hit = query((cx, cy), (cx + dx * C.R_RAY, cy + dy * C.R_RAY), 0.0, self._filter)
                out[k] = out[k + 1] = 0.0
                if hit is None:
                    rays[i] = (1.0, None)
                else:
                    kind = HIT_KINDS.get(hit.shape.collision_type, "obstacle")
                    out[k + (kind == "predator")] = 1.0 - hit.alpha
                    rays[i] = (hit.alpha, kind)
                k += 2

        # Аура найближчого (з максимальною аурою) хижака: [a, a·sin β, a·cos β] (§4.2)
        if C.SENSE_AURA:
            aura, bearing = 0.0, 0.0
            for p in self.predators:
                px, py = p.body.position
                dx, dy = px - cx, py - cy
                value = 1.0 - math.hypot(dx, dy) / (C.R_AURA_SENSE_FACTOR * p.cfg.r_aura)
                if value > aura:
                    aura = value
                    bearing = math.atan2(-dx * sin_p + dy * cos_p, dx * cos_p + dy * sin_p)
            self.aura, self.bearing = aura, bearing
            out[k] = aura
            out[k + 1] = aura * math.sin(bearing)
            out[k + 2] = aura * math.cos(bearing)
            k += 3

        # Пропріоцепція суглобів: [sin ψ, cos ψ, ω/OMEGA_MAX], ψ = (π/2)·θ̂ (§4.3)
        for seg, (pos_scale, neg_scale) in zip(self.creature.segments, self._scales):
            theta = seg.joint_angle
            norm = theta * (pos_scale if theta >= 0 else neg_scale)
            psi = half_pi * min(1.0, max(-1.0, norm))
            out[k] = math.sin(psi)
            out[k + 1] = math.cos(psi)
            out[k + 2] = seg.joint_rate * inv_omega
            k += 3

        # Тіло: локальна швидкість, кутова швидкість, орієнтація (§4.3)
        vx, vy = body.velocity
        out[k] = (vx * cos_p + vy * sin_p) / C.V_NORM
        out[k + 1] = (-vx * sin_p + vy * cos_p) / C.V_NORM
        out[k + 2] = body.angular_velocity * inv_omega
        out[k + 3] = sin_p
        out[k + 4] = cos_p
        k += 5

        # Осцилятор — центральний генератор ритму (§4.3)
        phase = 2 * math.pi * C.OSC_FREQ * t
        out[k] = math.sin(phase)
        out[k + 1] = math.cos(phase)
        return out
