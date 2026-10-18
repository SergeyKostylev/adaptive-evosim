"""Єдине місце для всіх констант-дефолтів проєкту (PROJECT_SPEC.md §13).

Жодне «магічне число» не живе в інших модулях — тільки імпорт звідси або
значення, що прийшло в тілі запиту.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Фізика (§2.1, §2.3) ---------------------------------------------------
DT: float = 1.0 / 120.0           # крок фізики, с
CONTROL_EVERY: int = 6            # керування мозком кожні N кроків (20 Гц)
OMEGA_MAX: float = 6.0            # рад/с, масштаб виходу мережі на motor.rate
V_MAX: float = 50.0               # жорсткий кламп швидкості тіл після кроку
F_MAX: float = 500.0              # кламп сили на точку вибірки
N_S: int = 3                      # точок вибірки гідросил на сегмент
MIN_SEGMENT_MASS: float = 0.1

RHO: float = 1.0                  # густина рідини
GRAVITY: tuple[float, float] = (0.0, 0.0)   # R1: нейтральна плавучість
LINEAR_DAMPING: float = 0.98      # space.damping
C_D_PERP: float = 2.0             # опір поперек осі сегмента
C_D_PAR: float = 0.1              # опір уздовж осі сегмента
C_L: float = 0.5                  # підйомна сила (0 = вимкнено)
C_D_BODY: float = 1.2             # ізотропний опір круглого тіла

# --- Світ (§7.2) -----------------------------------------------------------
W_WORLD: float = 60.0
H_WORLD: float = 40.0
#: Товщина стін світу. Стіни — теж перешкоди (§7.2); товсті, щоб тіло на
#: граничній швидкості (V_MAX·DT ≈ 0.42 од. за крок) не прошило їх наскрізь.
WALL_THICKNESS: float = 0.5

# --- Категорії та групи колізій (§3) ---------------------------------------
CAT_CREATURE: int = 0b001
CAT_OBSTACLE: int = 0b010
CAT_PREDATOR: int = 0b100

# --- Морфологія: ліміти (§3, §9.1) -----------------------------------------
MAX_LIMBS: int = 6
MAX_SEGMENTS: int = 4
BODY_RADIUS_RANGE: tuple[float, float] = (0.3, 1.5)
BODY_VERTS_RANGE: tuple[int, int] = (3, 8)
DENSITY_RANGE: tuple[float, float] = (0.5, 3.0)
SEG_LENGTH_RANGE: tuple[float, float] = (0.3, 2.0)
SEG_WIDTH_RANGE: tuple[float, float] = (0.05, 0.4)
JOINT_ANGLE_MIN_RANGE: tuple[float, float] = (-2.5, 0.0)
JOINT_ANGLE_MAX_RANGE: tuple[float, float] = (0.0, 2.5)
MOTOR_TORQUE_RANGE: tuple[float, float] = (10.0, 500.0)
OBSTACLE_VERTS_RANGE: tuple[int, int] = (3, 10)

# --- Сенсори (§4) ----------------------------------------------------------
N_RAYS: int = 12
R_RAY: float = 12.0
V_NORM: float = 5.0               # нормалізація лінійної швидкості тіла
OSC_FREQ: float = 1.0             # Гц, центральний генератор ритму
R_AURA_SENSE_FACTOR: float = 1.5  # R_aura_sense = 1.5 · R_aura
N_PROPRIO_BODY: int = 5           # [vx, vy, ω, sin φ, cos φ]
N_OSC: int = 2                    # [sin, cos]
N_AURA: int = 3                   # [aura, aura·sin(bear), aura·cos(bear)]
N_RAY_CHANNELS: int = 2           # d_obs, d_pred
N_JOINT_CHANNELS: int = 3         # sin ψ, cos ψ, ω/OMEGA_MAX
#: Увімкнені групи сенсорів (§4.4, етапність). Пропріоцепція й осцилятор
#: увімкнені завжди; радар і аура приходять на етапі 4 разом із хижаком.
#: Від них залежить `N_in`, а отже й розмір геному.
SENSE_RADAR: bool = False
SENSE_AURA: bool = False

# --- Мозок (§5) ------------------------------------------------------------
HIDDEN_1: int = 16
HIDDEN_2: int = 16

# --- Геном та ГА (§6) ------------------------------------------------------
GENOME_INIT_SIGMA: float = 0.5
GENOME_CLAMP: float = 5.0
GA_POPULATION: int = 100
GA_GENERATIONS: int = 200
GA_ELITISM: int = 2
GA_TOURNAMENT_K: int = 3
GA_P_CROSSOVER: float = 0.9
GA_P_SWAP: float = 0.5
GA_P_MUTATION: float = 0.05
GA_MUTATION_SIGMA: float = 0.15
GA_EPISODES_PER_INDIVIDUAL: int = 2
GA_EPISODE_SECONDS: float = 30.0
GA_DIVERSITY_SAMPLE: int = 20
GA_CHECKPOINT_EVERY: int = 25
#: Процесів оцінки: env `WORKERS_EVAL` (§12), 0 = auto (`cpu_count − 1`);
#: 1 — оцінка в тому самому процесі, без пулу (тести, налагодження)
WORKERS_EVAL: int = int(os.getenv("WORKERS_EVAL", "0"))
#: Скільки задач пулу припадає на воркера за покоління: дрібніші чанки
#: вирівнюють навантаження, але кожен коштує пересилання між процесами
EVAL_CHUNKS_PER_WORKER: int = 4

# --- Фітнес (§7.1) ---------------------------------------------------------
W_SURVIVAL: float = 3.0
W_DISTANCE: float = 1.5
W_DANGER: float = 2.0
W_COLLISION: float = 1.0
W_PASSIVITY: float = 1.0
W_ENERGY: float = 0.3
D_NORM: float = 20.0              # нормалізація чистого зміщення
V_MIN_PASSIVITY: float = 0.3      # v_min у компоненті Pas
N_HITS_NORM: int = 10             # нормалізація кількості контактів
COLLISION_DEBOUNCE: float = 0.25  # с, на пару шейпів
#: Компоненти, що входять у F на поточному етапі (§7.1, етапність); решта
#: звітуються як 0. Етап 4 додає S, G_aura, C.
FITNESS_COMPONENTS: frozenset[str] = frozenset({"D", "Pas", "E_n"})
FITNESS_KEYS: tuple[str, ...] = ("S", "D", "G_aura", "C", "Pas", "E_n")

# --- Хижак (§8.1) ----------------------------------------------------------
PREDATOR_ENABLED: bool = True
R_PREDATOR_BODY: float = 0.8
PREDATOR_BODY_RANGE: tuple[float, float] = (0.2, 3.0)
V_PATROL: float = 2.0
V_CHASE: float = 3.5
R_DETECT: float = 8.0
R_AURA: float = 5.0
R_CATCH: float = 1.0
T_GIVEUP: float = 6.0
DEFAULT_WAYPOINTS: list[list[float]] = [[-15.0, -10.0], [15.0, -10.0], [15.0, 10.0], [-15.0, 10.0]]

# --- Тестовий контролер-синусоїда (§2.4) -----------------------------------
DEMO_SWIM_FREQ: float = 1.0       # Гц, rate_j = sin(2π·f·t + φ_j)·OMEGA_MAX

# --- Live-канал (§9.5) -----------------------------------------------------
LIVE_FPS: int = 60
LIVE_QUEUE_DEPTH: int = 1         # відстаючий клієнт бачить лише найновіший кадр
PREVIEW_SPEED_RANGE: tuple[float, float] = (0.1, 8.0)
PREVIEW_EPISODE_SECONDS: float = 30.0

# --- Збереження (§11) ------------------------------------------------------
# Корінь даних: у Docker це змонтований том /app/experiments (див. §12).
# Локально задається через EVOSIM_DATA_DIR, інакше — `experiments/` від CWD.
DATA_DIR: Path = Path(os.getenv("EVOSIM_DATA_DIR", "experiments")).resolve()
RUNS_SUBDIR: str = "runs"
#: Завершені прогони (усі покоління, без помилки) копіюються сюди — і в git (§11)
ARCHIVE_SUBDIR: str = "archive"
#: Що з прогону йде в архів: легке й незамінне; чекпоінти й графіки — ні
ARCHIVE_FILES: tuple[str, ...] = ("config.json", "metrics.csv", "timing.csv", "best_genome.json")
EDITOR_SUBDIR: str = "editor"       # стан конструктора між сеансами
RUNS_DIR: str = "experiments/runs"
RUN_ID_TIME_FMT: str = "%Y%m%d-%H%M"
#: Скільки останніх подій тримає черга клієнта `/ws/metrics`
METRICS_QUEUE_DEPTH: int = 256
PLOT_DPI: int = 200


# --- Похідні величини (§4.4, §5) -------------------------------------------
def n_in_from_joints(n_joints: int) -> int:
    """N_in = N_rays·2 + 3 + N_joints·3 + 5 + 2 (§4.4) — лише за увімкненими
    групами сенсорів: вимкнена група з вектора входів просто випадає."""
    return (
        (N_RAYS * N_RAY_CHANNELS if SENSE_RADAR else 0)
        + (N_AURA if SENSE_AURA else 0)
        + n_joints * N_JOINT_CHANNELS
        + N_PROPRIO_BODY
        + N_OSC
    )


def genome_size(n_in: int, n_joints: int) -> int:
    """G = 16·N_in + 16 + 16·16 + 16 + N_joints·16 + N_joints (§5)."""
    return (
        HIDDEN_1 * n_in
        + HIDDEN_1
        + HIDDEN_1 * HIDDEN_2
        + HIDDEN_2
        + n_joints * HIDDEN_2
        + n_joints
    )
