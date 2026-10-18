"""Вектор входів мережі: radar-raycast, аура-сенсор, пропріоцепція,
осцилятор (§4). Пропріоцепція+осцилятор — етап 3, радар і аура — етап 4.

Усе — у локальній системі істоти, щоб поведінка не залежала від того, де у
світі й під яким кутом вона опинилась. Порядок груп у векторі фіксований
(радар, аура, суглоби, тіло, осцилятор); вимкнена група випадає (§4.4).
Вектор читається лише на control-тиках — мозку між ними він не потрібен.
"""

from __future__ import annotations

import math

import numpy as np

from .. import config as C
from .creature import Creature


def _side_scale(limit: float) -> float:
    """`1/|limit|` для нормалізації θ; межа нульової ширини дає 0 (нейтраль)."""
    return 1.0 / abs(limit) if abs(limit) > 1e-9 else 0.0


class Sensors:
    """Збирає вектор входів довжини `N_in` для однієї істоти."""

    def __init__(self, creature: Creature):
        if C.SENSE_RADAR or C.SENSE_AURA:
            raise NotImplementedError("радар і аура-сенсор — етап 4 (§4.1, §4.2)")
        self.creature = creature
        self.n_in = C.n_in_from_joints(creature.n_joints)
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
        body = self.creature.body
        phi = body.angle
        cos_p, sin_p = math.cos(phi), math.sin(phi)
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
