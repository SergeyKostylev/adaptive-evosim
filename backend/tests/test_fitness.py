"""Компоненти фітнесу на синтетичних траєкторіях, без фізики (§7, §15).

Етап 3 покриває `D`, `Pas`, `E_n` і склад формули; `S`, `G_aura`, `C`
додаються разом із хижаком на етапі 4.
"""

from __future__ import annotations

import pytest

from app import config as C
from app.evolution.fitness import FitnessAccumulator, combine
from app.schemas import FitnessWeights

T = 30.0
STEPS = round(T / C.DT)


def run(speed: float, u_abs_sum: float, n_joints: int = 4, steps: int = STEPS,
        active=frozenset(C.FITNESS_KEYS)) -> FitnessAccumulator:
    acc = FitnessAccumulator(T, n_joints, (0.0, 0.0), active=active)
    for _ in range(steps):
        acc.step(speed, u_abs_sum)
    return acc


def test_motionless_creature_gets_full_passivity():
    comps = run(0.0, 0.0).components(T, (0.0, 0.0))
    assert comps["Pas"] == 1.0
    assert comps["D"] == 0.0
    assert comps["E_n"] == 0.0


def test_passivity_scales_with_mean_speed():
    half = run(C.V_MIN_PASSIVITY / 2, 0.0).components(T, (0.0, 0.0))
    assert half["Pas"] == pytest.approx(0.5)
    fast = run(C.V_MIN_PASSIVITY * 3, 0.0).components(T, (0.0, 0.0))
    assert fast["Pas"] == 0.0


def test_distance_is_net_displacement_and_saturates():
    acc = run(1.0, 0.0)
    assert acc.components(T, (6.0, 8.0))["D"] == pytest.approx(10.0 / C.D_NORM)
    assert acc.components(T, (100.0, 0.0))["D"] == 1.0
    # Кружляння: великий шлях, але повернулись у старт → D = 0
    assert acc.components(T, (0.0, 0.0))["D"] == 0.0


def test_energy_is_mean_absolute_command():
    n = 4
    full = run(0.0, u_abs_sum=1.0 * n, n_joints=n).components(T, (0, 0))
    assert full["E_n"] == pytest.approx(1.0)
    half = run(0.0, u_abs_sum=0.5 * n, n_joints=n).components(T, (0, 0))
    assert half["E_n"] == pytest.approx(0.5)


def test_energy_normalized_by_episode_length_not_lifetime():
    """§7.1: `1/T_episode`, тож епізод, що урвався на половині, — половина енергії."""
    acc = run(0.0, u_abs_sum=4.0, n_joints=4, steps=STEPS // 2)
    assert acc.components(T / 2, (0, 0))["E_n"] == pytest.approx(0.5)


def test_combine_follows_formula_with_weights():
    comps = {"S": 1.0, "D": 0.5, "G_aura": 0.2, "C": 0.1, "Pas": 0.3, "E_n": 0.4}
    w = FitnessWeights()
    expected = (w.survival * 1.0 + w.distance * 0.5 - w.danger * 0.2
                - w.collision * 0.1 - w.passivity * 0.3 - w.energy * 0.4)
    assert combine(comps, w, frozenset(C.FITNESS_KEYS)) == pytest.approx(expected)


def test_inactive_components_reported_as_zero_and_excluded():
    """Етапність §7.1: на етапі 3 `S` не входить у F, навіть якщо істота жива."""
    acc = run(1.0, 2.0)
    acc.active = frozenset({"D", "Pas", "E_n"})
    f, comps = acc.result(T, (4.0, 0.0))
    assert comps["S"] == comps["G_aura"] == comps["C"] == 0.0
    w = FitnessWeights()
    assert f == pytest.approx(w.distance * comps["D"] - w.passivity * comps["Pas"]
                              - w.energy * comps["E_n"])


def test_stage3_active_set():
    assert C.FITNESS_COMPONENTS == {"D", "Pas", "E_n"}
