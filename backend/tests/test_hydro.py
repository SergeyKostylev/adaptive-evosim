"""Юніт-тести чистої математики гідросил (PROJECT_SPEC.md §2.2).

Жодного Pymunk: формули мають бути перевіряними без фізичного рушія.
"""

from __future__ import annotations

import numpy as np
import pytest

from app import config as C
from app.core import hydro


def test_sample_offsets_are_thirds_centres():
    """При N_s = 3 точки — центри третин: (−L/3, 0, +L/3) (§2.2)."""
    offsets = hydro.sample_offsets(1.2, 3)
    assert offsets == pytest.approx([-0.4, 0.0, 0.4])
    assert hydro.sample_offsets(2.0, 1) == pytest.approx([0.0])
    # Симетрія відносно центра за будь-якого N_s
    for n in (2, 3, 5, 7):
        assert hydro.sample_offsets(1.7, n).sum() == pytest.approx(0.0)


def test_drag_anisotropy_at_least_10x():
    """Рух поперек осі гальмується щонайменше в 10 разів сильніше (§2.1)."""
    speed = 2.0
    across = hydro.segment_point_forces([[0.0, speed]], angle=0.0, length_k=0.4, c_l=0.0)
    along = hydro.segment_point_forces([[speed, 0.0]], angle=0.0, length_k=0.4, c_l=0.0)
    ratio = np.linalg.norm(across) / np.linalg.norm(along)
    assert ratio >= 10.0
    # Точне значення — відношення коефіцієнтів
    assert ratio == pytest.approx(C.C_D_PERP / C.C_D_PAR)


def test_drag_opposes_motion_and_is_quadratic():
    f1 = hydro.segment_point_forces([[0.0, 1.0]], angle=0.0, length_k=0.4, c_l=0.0)[0]
    f2 = hydro.segment_point_forces([[0.0, 2.0]], angle=0.0, length_k=0.4, c_l=0.0)[0]
    assert f1[1] < 0 and f2[1] < 0                     # проти руху
    assert np.linalg.norm(f2) == pytest.approx(4 * np.linalg.norm(f1))   # ~v²


def test_zero_velocity_gives_zero_force():
    f = hydro.segment_point_forces([[0.0, 0.0], [1e-9, 0.0]], angle=0.7, length_k=0.4)
    assert np.allclose(f, 0.0)


def test_force_clamped_to_f_max():
    """Кламп на точку — головний захист від вибуху при поганих геномах (§2.3)."""
    f = hydro.segment_point_forces([[0.0, 500.0]], angle=0.0, length_k=0.4)[0]
    assert np.linalg.norm(f) == pytest.approx(C.F_MAX)
    assert np.linalg.norm(f) <= C.F_MAX + 1e-9


def test_lift_vanishes_at_zero_and_right_angle():
    """sin(2α) = 0 при α = 0 та α = 90° — підйомної сили немає (§2.2)."""
    for v in ([3.0, 0.0], [0.0, 3.0]):
        with_lift = hydro.segment_point_forces([v], angle=0.0, length_k=0.4, c_l=C.C_L)[0]
        without = hydro.segment_point_forces([v], angle=0.0, length_k=0.4, c_l=0.0)[0]
        assert with_lift == pytest.approx(without)


def test_lift_is_perpendicular_to_flow():
    v = np.array([2.0, 2.0])
    with_lift = hydro.segment_point_forces([v], angle=0.0, length_k=0.4, c_l=C.C_L)[0]
    without = hydro.segment_point_forces([v], angle=0.0, length_k=0.4, c_l=0.0)[0]
    lift = with_lift - without
    assert np.linalg.norm(lift) > 0
    assert float(lift @ v) == pytest.approx(0.0, abs=1e-9)


def test_c_l_zero_disables_lift():
    v = [2.0, 1.0]
    assert hydro.segment_point_forces([v], angle=0.3, length_k=0.4, c_l=0.0) == pytest.approx(
        hydro.segment_point_forces([v], angle=0.3, length_k=0.4, c_l=0.0))


@pytest.mark.parametrize("n_s,tolerance", [(3, 0.25), (7, 0.15)])
def test_rotational_drag_converges_to_analytic(n_s, tolerance):
    """Сумарний момент точкових сил збігається до аналітики при N_s → 7 (§2.2).

    Окремого моменту в'язкого тертя немає: він виникає з того, що сили
    прикладаються в кількох точках із локальними швидкостями.
    """
    length, omega = 1.6, 3.0
    offsets = hydro.sample_offsets(length, n_s)
    # Стрижень уздовж осі X обертається навколо свого центра: v = ω × r
    velocities = np.stack([np.zeros_like(offsets), omega * offsets], axis=1)
    forces = hydro.segment_point_forces(
        velocities, angle=0.0, length_k=length / n_s, c_l=0.0)
    torque = float(np.sum(offsets * forces[:, 1]))      # r × F для осі X

    analytic = hydro.rod_rotational_torque(omega, length)
    assert torque < 0 and analytic < 0                  # момент гальмує обертання
    assert abs(torque - analytic) / abs(analytic) <= tolerance


def test_rotational_drag_error_shrinks_with_more_points():
    length, omega = 1.6, 3.0
    analytic = hydro.rod_rotational_torque(omega, length)

    def rel_error(n_s: int) -> float:
        offsets = hydro.sample_offsets(length, n_s)
        velocities = np.stack([np.zeros_like(offsets), omega * offsets], axis=1)
        forces = hydro.segment_point_forces(
            velocities, angle=0.0, length_k=length / n_s, c_l=0.0)
        return abs(float(np.sum(offsets * forces[:, 1])) - analytic) / abs(analytic)

    assert rel_error(7) < rel_error(3) < 0.25


def test_segment_force_is_rotation_equivariant():
    """Поворот сегмента й швидкості разом має повертати ту саму силу."""
    v = np.array([[1.3, -0.7]])
    base = hydro.segment_point_forces(v, angle=0.0, length_k=0.4)[0]
    phi = 0.9
    rot = np.array([[np.cos(phi), -np.sin(phi)], [np.sin(phi), np.cos(phi)]])
    turned = hydro.segment_point_forces(v @ rot.T, angle=phi, length_k=0.4)[0]
    assert turned == pytest.approx(rot @ base, abs=1e-9)


def test_body_drag_is_isotropic_and_opposes_motion():
    speed = 2.5
    for direction in ([1, 0], [0, 1], [0.6, 0.8]):
        v = np.array(direction, dtype=float) * speed
        f = hydro.body_drag_force(v, radius=0.8)
        assert float(f @ v) < 0
        assert np.linalg.norm(f) == pytest.approx(
            0.5 * C.RHO * C.C_D_BODY * 1.6 * speed**2)


def test_body_drag_zero_below_epsilon():
    assert np.allclose(hydro.body_drag_force([0.0, 0.0], 0.8), 0.0)


def test_body_torque_opposes_spin():
    assert hydro.body_drag_torque(2.0, 0.8) < 0
    assert hydro.body_drag_torque(-2.0, 0.8) > 0
    assert hydro.body_drag_torque(0.0, 0.8) == 0.0


def test_clamp_magnitude_keeps_direction():
    v = np.array([[3.0, 4.0]])
    out = hydro.clamp_magnitude(v, 1.0)
    assert np.linalg.norm(out) == pytest.approx(1.0)
    assert out[0] == pytest.approx([0.6, 0.8])
    # Короткий вектор лишається недоторканим
    short = np.array([[0.3, 0.4]])
    assert hydro.clamp_magnitude(short, 1.0) == pytest.approx(short)
