"""Чиста математика гідросил: drag/lift сегмента та опір круглого тіла (§2.2).

Тільки NumPy, без Pymunk у формулах — модуль юніт-тестується окремо
(`tests/test_hydro.py`).

Домовленість про розмірності: «площа» в 2D — це довжина на одиницю умовної
глибини. Для сегмента довжиною `L`, розбитого на `N_s` точок, кожній точці
відповідає `A_k = L_k = L / N_s`; ширина `w` у гідросили не входить (вона
впливає лише на масу й момент інерції в `creature.py`).
"""

from __future__ import annotations

import math

import numpy as np

from .. import config as C

EPS = 1e-6      # нижче цієї швидкості сила вважається нульовою (§2.2, крок 1)


def sample_offsets(length: float, n_s: int = C.N_S) -> np.ndarray:
    """Зсуви точок вибірки вздовж осі сегмента, від його центра.

    Центри `n_s` рівних частин: при `n_s = 3` це `(-L/3, 0, +L/3)`. Саме
    центри, а не кінці — це правило середніх прямокутників, яке й забезпечує
    збіжність сумарного моменту до аналітики (§2.2).
    """
    k = np.arange(n_s, dtype=np.float64)
    return (k + 0.5) * (length / n_s) - length / 2.0


def clamp_magnitude(vectors: np.ndarray, limit: float) -> np.ndarray:
    """Обрізає довжину кожного вектора до `limit`, не змінюючи напрямку."""
    v = np.atleast_2d(np.asarray(vectors, dtype=np.float64))
    mag = np.linalg.norm(v, axis=1)
    scale = np.ones_like(mag)
    hot = mag > limit
    scale[hot] = limit / mag[hot]
    out = v * scale[:, None]
    return out.reshape(np.shape(vectors))


def point_force(
    vx: float, vy: float,
    cos_a: float, sin_a: float,
    length_k: float,
    rho: float, c_d_perp: float, c_d_par: float, c_l: float, f_max: float,
) -> tuple[float, float]:
    """Сила в **одній** точці вибірки (§2.2, кроки 2–6) — скалярне ядро.

    Єдине джерело правди для гідросил сегмента: і векторна обгортка
    `segment_point_forces` (зручна в тестах), і рівнодійні для фізики
    (`segment_resultants`) рахуються через неї.

    Чисті float, а не NumPy: масиви тут завжди крихітні (`N_s = 3`), і для них
    накладні витрати NumPy у рази перевищують саму арифметику — а це найгарячіше
    місце headless-оцінки (§1.2).
    """
    speed_sq = vx * vx + vy * vy
    if speed_sq <= EPS * EPS:
        return 0.0, 0.0

    # Проєкції на вісь сегмента t̂ = (cos, sin) та нормаль n̂ = (−sin, cos)
    v_t = vx * cos_a + vy * sin_a
    v_n = -vx * sin_a + vy * cos_a

    # Опір: квадратичний, окремо вздовж осі та поперек неї — саме ця
    # анізотропія (C_d_perp ≫ C_d_par) і робить плавання можливим
    k = -0.5 * rho * length_k
    drag_t = k * c_d_par * abs(v_t) * v_t
    drag_n = k * c_d_perp * abs(v_n) * v_n
    fx = drag_t * cos_a - drag_n * sin_a
    fy = drag_t * sin_a + drag_n * cos_a

    if c_l > 0.0:
        # |v|²·2·sinα·cosα = 2·v_n·v_t, а l̂ = perp(v)/|v|·sign(sinα·cosα),
        # тож 0.5·rho·C_l·L_k·|v|²·2 sinα cosα · l̂ зводиться до виразу нижче
        # без окремої гілки для |v| ≈ 0
        product = v_n * v_t
        sign = 1.0 if product > 0.0 else (-1.0 if product < 0.0 else 0.0)
        lift = rho * c_l * length_k * product * sign / math.sqrt(speed_sq)
        fx += lift * -vy
        fy += lift * vx

    # Кламп на точку (§2.2, крок 6) — захист від вибуху при поганих геномах
    mag_sq = fx * fx + fy * fy
    if mag_sq > f_max * f_max:
        scale = f_max / math.sqrt(mag_sq)
        fx *= scale
        fy *= scale
    return fx, fy


def segment_resultants(
    states,
    *,
    rho: float = C.RHO,
    c_d_perp: float = C.C_D_PERP,
    c_d_par: float = C.C_D_PAR,
    c_l: float = C.C_L,
    f_max: float = C.F_MAX,
) -> list[tuple[float, float, float]]:
    """Рівнодійна та її момент для кожного сегмента: `(ΣF_x, ΣF_y, Σ r_k × F_k)`.

    `states` — послідовність `(angle, vx, vy, omega, offsets, length_k)`, де
    `vx, vy, omega` — швидкість і кутова швидкість **центра мас** сегмента, а
    `offsets` — зсуви точок вибірки вздовж його осі.

    Прикладання рівнодійної в центрі мас разом із сумарним моментом тотожне
    прикладанню кожної сили у своїй точці, але коштує два переходи в C замість
    `N_s` (див. `physics.apply_fluid_forces`).
    """
    out = []
    for angle, vx0, vy0, omega, offsets, length_k in states:
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        fx_sum = fy_sum = torque = 0.0
        for offset in offsets:
            rx = offset * cos_a
            ry = offset * sin_a
            # Швидкість точки = швидкість центра мас + ω × r (рідина нерухома)
            fx, fy = point_force(
                vx0 - omega * ry, vy0 + omega * rx,
                cos_a, sin_a, length_k,
                rho, c_d_perp, c_d_par, c_l, f_max,
            )
            fx_sum += fx
            fy_sum += fy
            torque += rx * fy - ry * fx
        out.append((fx_sum, fy_sum, torque))
    return out


def segment_point_forces(
    velocities: np.ndarray,
    angle: float,
    length_k: float,
    *,
    rho: float = C.RHO,
    c_d_perp: float = C.C_D_PERP,
    c_d_par: float = C.C_D_PAR,
    c_l: float = C.C_L,
    f_max: float = C.F_MAX,
) -> np.ndarray:
    """Сили в точках вибірки одного сегмента, `(N, 2)` — обгортка `point_force`.

    Зручна форма для юніт-тестів і для будь-якого коду, що міркує масивами;
    на гарячому шляху використовується `segment_resultants`.
    """
    v = np.atleast_2d(np.asarray(velocities, dtype=np.float64))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return np.array(
        [point_force(float(vx), float(vy), cos_a, sin_a, length_k,
                     rho, c_d_perp, c_d_par, c_l, f_max) for vx, vy in v],
        dtype=np.float64,
    ).reshape(v.shape)


def body_drag_force(
    velocity: np.ndarray,
    radius: float,
    *,
    rho: float = C.RHO,
    c_d_body: float = C.C_D_BODY,
    f_max: float = C.F_MAX,
) -> np.ndarray:
    """Ізотропний опір круглого тіла: `F = −0.5·rho·C_d·(2r)·|v|·v` (§2.2)."""
    v = np.asarray(velocity, dtype=np.float64)
    speed = float(np.linalg.norm(v))
    if speed <= EPS:
        return np.zeros(2)
    return clamp_magnitude(-0.5 * rho * c_d_body * (2.0 * radius) * speed * v, f_max)


def body_drag_torque(
    omega: float,
    radius: float,
    *,
    rho: float = C.RHO,
    c_d_body: float = C.C_D_BODY,
) -> float:
    """Обертальне демпфування диска: `τ = −rho·C_d·r⁴·|ω|·ω` (§2.2)."""
    return -rho * c_d_body * radius**4 * abs(omega) * omega


def rod_rotational_torque(
    omega: float,
    length: float,
    *,
    rho: float = C.RHO,
    c_d_perp: float = C.C_D_PERP,
) -> float:
    """Аналітичний момент опору стрижня, що обертається навколо центра.

    `τ = −rho·C_d_perp·|ω|·ω·L⁴/64` — інтеграл того самого нормального опору
    по довжині. Використовується **лише** в `test_hydro.py` як еталон збіжності
    точкової дискретизації (§2.2); у симуляції момент виникає сам собою з
    того, що сили прикладаються в кількох точках уздовж осі.
    """
    return -rho * c_d_perp * abs(omega) * omega * length**4 / 64.0
