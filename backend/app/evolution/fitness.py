"""FitnessAccumulator і фіналізація компонентів S, D, G_aura, C, Pas, E_n (§7).
D/Pas/E_n — етап 3, повна формула — етап 4.

Акумулятор нічого не знає про Pymunk: `World` щокроку віддає йому числа
(швидкість центра, сумарний модуль команд), тож формули тестуються на
синтетичних траєкторіях без фізики (§15).
"""

from __future__ import annotations

import math

from .. import config as C
from ..schemas import FitnessWeights

#: Компонент → поле `FitnessWeights` і знак у формулі §7.1
TERMS: dict[str, tuple[str, float]] = {
    "S": ("survival", +1.0),
    "D": ("distance", +1.0),
    "G_aura": ("danger", -1.0),
    "C": ("collision", -1.0),
    "Pas": ("passivity", -1.0),
    "E_n": ("energy", -1.0),
}


def combine(components: dict[str, float], weights: FitnessWeights,
            active: frozenset[str] = C.FITNESS_COMPONENTS) -> float:
    """`F = w_surv·S + w_dist·D − w_dang·G_aura − w_coll·C − w_pass·Pas − w_enrg·E_n`
    лише за активними на цьому етапі компонентами (§7.1, етапність)."""
    total = 0.0
    for key in C.FITNESS_KEYS:
        if key in active:
            field, sign = TERMS[key]
            total += sign * getattr(weights, field) * components[key]
    return total


class FitnessAccumulator:
    """Накопичує інтеграли епізоду щокроку, фіналізує в кінці (§7.1)."""

    def __init__(self, episode_seconds: float, n_joints: int,
                 start: tuple[float, float], weights: FitnessWeights | None = None,
                 active: frozenset[str] = C.FITNESS_COMPONENTS):
        self.episode_seconds = float(episode_seconds)
        self.n_joints = n_joints
        self.start = (float(start[0]), float(start[1]))
        self.weights = weights or FitnessWeights()
        self.active = active
        self.steps = 0
        self.speed_sum = 0.0
        self.energy_sum = 0.0            # Σ_t Σ_j |u_j(t)|·DT

    def step(self, speed: float, u_abs_sum: float) -> None:
        """Один фізичний крок: модуль швидкості центра і `Σ_j |u_j|`."""
        self.steps += 1
        self.speed_sum += speed
        self.energy_sum += u_abs_sum * C.DT

    def components(self, t_alive: float, position: tuple[float, float]) -> dict[str, float]:
        """Компоненти §7.1; неактивні на цьому етапі — нулі."""
        T = self.episode_seconds
        disp = math.hypot(position[0] - self.start[0], position[1] - self.start[1])
        v_mean = self.speed_sum / self.steps if self.steps else 0.0
        values = {
            "S": min(t_alive / T, 1.0),
            "D": min(disp / C.D_NORM, 1.0),
            "G_aura": 0.0,
            "C": 0.0,
            "Pas": max(0.0, 1.0 - v_mean / C.V_MIN_PASSIVITY),
            "E_n": self.energy_sum / (T * self.n_joints) if self.n_joints else 0.0,
        }
        return {k: (v if k in self.active else 0.0) for k, v in values.items()}

    def result(self, t_alive: float, position: tuple[float, float]) -> tuple[float, dict[str, float]]:
        """`(F, компоненти)` — і фінальний підсумок, і running-значення для HUD."""
        comps = self.components(t_alive, position)
        return combine(comps, self.weights, self.active), comps
