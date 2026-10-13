"""Pydantic-моделі всіх JSON-контрактів (PROJECT_SPEC.md §9).

Будь-яка зміна контракту вноситься спершу в PROJECT_SPEC.md, потім сюди.
Ліміти та дефолти беруться виключно з `config.py`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config as C

Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]


class _Model(BaseModel):
    """Спільна база: заборонені невідомі поля — контракт жорсткий."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# §9.1 CreatureConfig
# ---------------------------------------------------------------------------
class JointConfig(_Model):
    angle_min: float = Field(..., ge=C.JOINT_ANGLE_MIN_RANGE[0], le=C.JOINT_ANGLE_MIN_RANGE[1])
    angle_max: float = Field(..., ge=C.JOINT_ANGLE_MAX_RANGE[0], le=C.JOINT_ANGLE_MAX_RANGE[1])
    motor_torque: float = Field(..., ge=C.MOTOR_TORQUE_RANGE[0], le=C.MOTOR_TORQUE_RANGE[1])

    @model_validator(mode="after")
    def _non_degenerate(self) -> "JointConfig":
        if self.angle_max - self.angle_min < 1e-6:
            raise ValueError("діапазон суглоба порожній: потрібно angle_max > angle_min")
        return self


class SegmentConfig(_Model):
    length: float = Field(..., ge=C.SEG_LENGTH_RANGE[0], le=C.SEG_LENGTH_RANGE[1])
    width: float = Field(..., ge=C.SEG_WIDTH_RANGE[0], le=C.SEG_WIDTH_RANGE[1])
    density: float = Field(default=1.0, ge=C.DENSITY_RANGE[0], le=C.DENSITY_RANGE[1])
    joint: JointConfig


class LimbConfig(_Model):
    attach_angle: float = Field(..., description="кут точки кріплення на периметрі тіла, рад")
    rest_angle: float = Field(default=0.0, description="базова орієнтація кінцівки, рад")
    segments: list[SegmentConfig] = Field(..., min_length=1, max_length=C.MAX_SEGMENTS)


class BodyConfig(_Model):
    shape: Literal["circle", "polygon"]
    radius: float | None = Field(default=None)
    vertices: list[Vec2] | None = Field(default=None)
    density: float = Field(default=1.0, ge=C.DENSITY_RANGE[0], le=C.DENSITY_RANGE[1])

    @model_validator(mode="after")
    def _shape_consistency(self) -> "BodyConfig":
        if self.shape == "circle":
            if self.radius is None:
                raise ValueError("для shape='circle' потрібен radius")
            lo, hi = C.BODY_RADIUS_RANGE
            if not lo <= self.radius <= hi:
                raise ValueError(f"radius має бути в межах [{lo}, {hi}]")
            if self.vertices is not None:
                raise ValueError("для shape='circle' vertices має бути null")
        else:
            if self.radius is not None:
                raise ValueError("для shape='polygon' radius має бути null")
            if self.vertices is None:
                raise ValueError("для shape='polygon' потрібні vertices")
            lo, hi = C.BODY_VERTS_RANGE
            if not lo <= len(self.vertices) <= hi:
                raise ValueError(f"полігон тіла має містити {lo}–{hi} вершин")
            if not is_convex(self.vertices):
                raise ValueError("полігон тіла має бути опуклим і невиродженим")
        return self


class CreatureConfig(_Model):
    name: str = Field(default="creature", min_length=1, max_length=64)
    body: BodyConfig
    limbs: list[LimbConfig] = Field(..., min_length=1, max_length=C.MAX_LIMBS)

    # --- похідні величини (§4.4, §5, §6.1) ---
    @property
    def n_joints(self) -> int:
        """Один суглоб на сегмент: тіло↔сегмент₀ і далі сегментᵢ₋₁↔сегментᵢ."""
        return sum(len(limb.segments) for limb in self.limbs)

    @property
    def n_in(self) -> int:
        return C.n_in_from_joints(self.n_joints)

    @property
    def genome_size(self) -> int:
        return C.genome_size(self.n_in, self.n_joints)

    @property
    def creature_hash(self) -> str:
        """SHA-256 канонічного JSON морфології (без `name` — перейменування
        не змінює морфологію, отже не ламає сумісність геному)."""
        morphology = self.model_dump(mode="json", exclude={"name"})
        canonical = json.dumps(morphology, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def derived(self) -> dict:
        return {
            "n_joints": self.n_joints,
            "n_in": self.n_in,
            "genome_size": self.genome_size,
            "creature_hash": self.creature_hash,
        }


# ---------------------------------------------------------------------------
# §9.2 EnvironmentConfig
# ---------------------------------------------------------------------------
class WorldConfig(_Model):
    width: float = Field(default=C.W_WORLD, gt=0)
    height: float = Field(default=C.H_WORLD, gt=0)


class FluidConfig(_Model):
    rho: float = Field(default=C.RHO, gt=0)
    c_d_perp: float = Field(default=C.C_D_PERP, ge=0)
    c_d_par: float = Field(default=C.C_D_PAR, ge=0)
    c_l: float = Field(default=C.C_L, ge=0)
    linear_damping: float = Field(default=C.LINEAR_DAMPING, gt=0, le=1)


class PredatorParams(_Model):
    """Сутність «хижак» без прив'язки до світу (§8.1).

    Маршрут патрулювання сюди не входить: він — геометрія конкретної сцени,
    тож живе в `EnvironmentConfig`. Завдяки цьому одного хижака можна ставити
    в різні сцени, а швидкість/радіуси лишаються незалежною віссю експерименту.
    """

    enabled: bool = C.PREDATOR_ENABLED
    r_body: float = Field(default=C.R_PREDATOR_BODY,
                          ge=C.PREDATOR_BODY_RANGE[0], le=C.PREDATOR_BODY_RANGE[1])
    v_patrol: float = Field(default=C.V_PATROL, gt=0)
    v_chase: float = Field(default=C.V_CHASE, gt=0)
    r_detect: float = Field(default=C.R_DETECT, gt=0)
    r_aura: float = Field(default=C.R_AURA, gt=0)
    r_catch: float = Field(default=C.R_CATCH, gt=0)

    @model_validator(mode="after")
    def _radii_order(self) -> "PredatorParams":
        if not self.r_catch <= self.r_aura <= self.r_detect:
            raise ValueError("має виконуватись r_catch ≤ r_aura ≤ r_detect")
        return self


class PredatorConfig(PredatorParams):
    """Хижак у сцені: параметри + маршрут + звідки взяті параметри."""

    #: Назва у бібліотеці (`/api/editor/predators/{name}`), якщо параметри
    #: скопійовані звідти. Прогін усе одно зберігає копію (§11), тож зміна
    #: бібліотеки не ламає відтворюваність уже запущених експериментів.
    preset: str | None = Field(default=None, max_length=64)
    waypoints: list[Vec2] = Field(default_factory=lambda: [list(w) for w in C.DEFAULT_WAYPOINTS], min_length=2)


class SpawnConfig(_Model):
    """Точка появи популяції у сцені (§9.2).

    Конкретна морфологія тут не вказується: ГА оцінює популяцію однієї істоти
    з `ExperimentConfig.creature`. Кілька точок дають різні стартові позиції
    для епізодів однієї особини (§7.3, `E = 2`).
    """

    position: Vec2
    angle: float = 0.0
    label: str | None = Field(default=None, max_length=64)


class StaticPolygonObstacle(_Model):
    type: Literal["static_polygon"]
    vertices: list[Vec2] = Field(..., min_length=C.OBSTACLE_VERTS_RANGE[0], max_length=C.OBSTACLE_VERTS_RANGE[1])
    position: Vec2
    angle: float = 0.0

    @field_validator("vertices")
    @classmethod
    def _convex(cls, v: list[list[float]]) -> list[list[float]]:
        if not is_convex(v):
            raise ValueError("полігон перешкоди має бути опуклим і невиродженим")
        return v


class StaticCircleObstacle(_Model):
    type: Literal["static_circle"]
    radius: float = Field(..., gt=0)
    position: Vec2


class MovingPolygonObstacle(_Model):
    type: Literal["moving_polygon"]
    vertices: list[Vec2] = Field(..., min_length=C.OBSTACLE_VERTS_RANGE[0], max_length=C.OBSTACLE_VERTS_RANGE[1])
    p1: Vec2
    p2: Vec2
    period: float = Field(..., gt=0)
    phase: float = 0.0

    @field_validator("vertices")
    @classmethod
    def _convex(cls, v: list[list[float]]) -> list[list[float]]:
        if not is_convex(v):
            raise ValueError("полігон перешкоди має бути опуклим і невиродженим")
        return v


Obstacle = Annotated[
    StaticPolygonObstacle | StaticCircleObstacle | MovingPolygonObstacle,
    Field(discriminator="type"),
]


class EnvironmentConfig(_Model):
    world: WorldConfig = Field(default_factory=WorldConfig)
    fluid: FluidConfig = Field(default_factory=FluidConfig)
    spawns: list[SpawnConfig] = Field(default_factory=list, max_length=16)
    predators: list[PredatorConfig] = Field(default_factory=list, max_length=8)
    obstacles: list[Obstacle] = Field(default_factory=list)
    #: Застаріле поле однієї особини-хижака; згортається у `predators`.
    #: `exclude=True` прибирає його з усіх форм серіалізації — і з
    #: `model_dump()`, і з `model_dump_json()`, яким користується FastAPI.
    predator: PredatorConfig | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _fold_legacy_predator(self) -> "EnvironmentConfig":
        """Сцени, збережені за старим контрактом (один `predator`), лишаються
        читабельними: поле переноситься в `predators` і більше не серіалізується."""
        if self.predator is not None:
            if not self.predators:
                self.predators = [self.predator]
            object.__setattr__(self, "predator", None)
        return self

    @property
    def enabled_predators(self) -> list[PredatorConfig]:
        return [p for p in self.predators if p.enabled]


# ---------------------------------------------------------------------------
# §9.3 ExperimentConfig
# ---------------------------------------------------------------------------
class GAConfig(_Model):
    population: int = Field(default=C.GA_POPULATION, ge=4, le=10_000)
    generations: int = Field(default=C.GA_GENERATIONS, ge=1, le=100_000)
    elitism: int = Field(default=C.GA_ELITISM, ge=0)
    tournament_k: int = Field(default=C.GA_TOURNAMENT_K, ge=2)
    p_crossover: float = Field(default=C.GA_P_CROSSOVER, ge=0, le=1)
    crossover: Literal["uniform"] = "uniform"
    p_mutation: float = Field(default=C.GA_P_MUTATION, ge=0, le=1)
    mutation_sigma: float = Field(default=C.GA_MUTATION_SIGMA, gt=0)
    episodes_per_individual: int = Field(default=C.GA_EPISODES_PER_INDIVIDUAL, ge=1, le=10)
    episode_seconds: float = Field(default=C.GA_EPISODE_SECONDS, gt=0)

    @model_validator(mode="after")
    def _elitism_fits(self) -> "GAConfig":
        if self.elitism >= self.population:
            raise ValueError("elitism має бути меншим за population")
        if self.tournament_k > self.population:
            raise ValueError("tournament_k не може перевищувати population")
        return self


class FitnessWeights(_Model):
    survival: float = Field(default=C.W_SURVIVAL, ge=0)
    distance: float = Field(default=C.W_DISTANCE, ge=0)
    danger: float = Field(default=C.W_DANGER, ge=0)
    collision: float = Field(default=C.W_COLLISION, ge=0)
    passivity: float = Field(default=C.W_PASSIVITY, ge=0)
    energy: float = Field(default=C.W_ENERGY, ge=0)


class ExperimentConfig(_Model):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-.]+$")
    seed: int = Field(default=42, ge=0)
    creature: CreatureConfig
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    ga: GAConfig = Field(default_factory=GAConfig)
    fitness_weights: FitnessWeights = Field(default_factory=FitnessWeights)


# ---------------------------------------------------------------------------
# §6.1 GenomeJSON
# ---------------------------------------------------------------------------
class GenomeJSON(_Model):
    creature_hash: str
    n_in: int = Field(..., ge=1)
    n_joints: int = Field(..., ge=1)
    weights: list[float]

    @model_validator(mode="after")
    def _size_matches(self) -> "GenomeJSON":
        expected = C.genome_size(self.n_in, self.n_joints)
        if len(self.weights) != expected:
            raise ValueError(f"довжина weights={len(self.weights)}, очікувалось {expected}")
        return self


# ---------------------------------------------------------------------------
# §9.4 Стан конструктора (чернетка між сеансами)
# ---------------------------------------------------------------------------
class EditorState(_Model):
    """Знімок робочого стану редактора.

    Поля `creature`/`environment` навмисно нетипізовані: це **чернетка**, яка
    може бути незавершеною (істота без кінцівок, сцена без хижака). Строга
    перевірка відбувається в `/api/creature/validate` та перед запуском ГА.
    """

    creature: dict
    environment: dict
    #: Сутність «хижак», що редагується (параметри без маршруту)
    predator: dict | None = None
    predator_name: str | None = Field(default=None, max_length=64)
    ga: dict | None = None
    fitness_weights: dict | None = None
    saved_at: str | None = None


class EditorStateResponse(BaseModel):
    exists: bool
    saved_at: str | None = None
    state: EditorState | None = None


class PresetMeta(BaseModel):
    """Опис сцени для списку в UI; `environment` потрібен для мініатюри."""

    name: str
    saved_at: str | None = None
    creature_name: str | None = None
    limbs: int = 0
    segments: int = 0
    obstacles: int = 0
    waypoints: int = 0
    bytes: int = 0
    environment: dict | None = None


class PresetList(BaseModel):
    presets: list[PresetMeta] = Field(default_factory=list)
    limit: int


class SavedPredator(_Model):
    """Елемент бібліотеки хижаків: лише параметри, без маршруту."""

    predator: PredatorParams
    saved_at: str | None = None


class PredatorMeta(BaseModel):
    name: str
    saved_at: str | None = None
    r_body: float = C.R_PREDATOR_BODY
    r_aura: float = C.R_AURA
    r_detect: float = C.R_DETECT
    v_chase: float = C.V_CHASE
    bytes: int = 0
    predator: PredatorParams | None = None


class PredatorLibrary(BaseModel):
    predators: list[PredatorMeta] = Field(default_factory=list)
    limit: int


class SavedCreature(_Model):
    """Елемент бібліотеки істот: лише морфологія (без сцени та параметрів ГА)."""

    creature: CreatureConfig
    saved_at: str | None = None


class CreatureMeta(BaseModel):
    """Рядок списку бібліотеки; `creature` потрібна фронтенду для мініатюри."""

    name: str
    saved_at: str | None = None
    creature_name: str | None = None
    limbs: int = 0
    segments: int = 0
    shape: str | None = None
    bytes: int = 0
    creature: CreatureConfig | None = None


class CreatureList(BaseModel):
    creatures: list[CreatureMeta] = Field(default_factory=list)
    limit: int


# ---------------------------------------------------------------------------
# Відповіді REST (§9.4)
# ---------------------------------------------------------------------------
class CreatureValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    n_joints: int | None = None
    n_in: int | None = None
    genome_size: int | None = None
    creature_hash: str | None = None


class PredatorValidationResponse(BaseModel):
    """Відповідь `POST /api/predator/validate` (§9.4)."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    #: Дотик до тіла теж ловить, тож ефективний радіус — max(r_catch, r_body) (§8.1)
    r_catch_effective: float | None = None
    aura_diameter: float | None = None


class SimulationStatus(BaseModel):
    state: Literal["RUNNING", "PAUSED", "STOPPED"]
    run_id: str | None = None
    generation: int = 0
    population: int = 0
    elapsed_s: float = 0.0
    mode: Literal["headless", "live"] = "headless"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# Геометрія
# ---------------------------------------------------------------------------
def is_convex(vertices: list[list[float]], eps: float = 1e-9) -> bool:
    """Опуклість простого полігона: усі векторні добутки одного знаку.

    Приймає як CW, так і CCW порядок вершин; відкидає вироджені (нульова площа)
    та колінеарні випадки.
    """
    n = len(vertices)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        cx, cy = vertices[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) <= eps:
            return False
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True
