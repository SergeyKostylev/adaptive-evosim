"""Фасад одного епізоду: `World(creature_cfg, env_cfg, genome, seed)` (§1.2).

`step()` виконує один крок `DT`: на control-тиках «сенсори → мозок → мотори»,
далі фізика й накопичення фітнесу. Етап 3 дає мозок (геном), пропріоцепцію з
осцилятором і компоненти `D`, `Pas`, `E_n`; FSM хижака, радар, аура та решта
фітнесу — етап 4, тож відповідні частини знімка поки що порожні (див. §9.5).

Без геному епізод крутиться на тестовій синусоїді (§2.4) — і так само рахує
фітнес, щоб базову лінію можна було порівняти з навченою істотою.
"""

from __future__ import annotations

import math

import numpy as np

from .. import config as C
from ..evolution.fitness import FitnessAccumulator
from ..schemas import CreatureConfig, EnvironmentConfig, FitnessWeights
from .brain import Brain
from .creature import Creature, build_creature
from .physics import PhysicsWorld
from .sensors import Sensors


class SineController:
    """Тестовий контролер-синусоїда (§2.4): `u_j = sin(2π·f·t + φ_j)`.

    `φ_j = (π/2)·k_j`, де `k_j` — номер сегмента **в межах кінцівки**: уздовж
    кінцівки біжить хвиля з чвертьперіодним запізненням, а кінцівки махають
    синхронно. Рознесення фаз за наскрізним індексом суглоба ставило б
    симетричну пару кінцівок у протифазу — істота крутилася б на місці
    замість пливти (§2.4).
    """

    def __init__(self, creature_cfg: CreatureConfig, freq: float = C.DEMO_SWIM_FREQ):
        self.freq = freq
        self.phases = np.array(
            [seg_i * math.pi / 2
             for limb in creature_cfg.limbs
             for seg_i in range(len(limb.segments))],
            dtype=np.float64,
        )

    def __call__(self, t: float, world: "World") -> np.ndarray:
        return np.sin(2 * math.pi * self.freq * t + self.phases)


class BrainController:
    """Контролер із геному: сенсори → MLP → команди моторів (§4, §5)."""

    def __init__(self, brain: Brain):
        self.brain = brain

    def __call__(self, t: float, world: "World") -> np.ndarray:
        return self.brain.forward(world.sensors.read(t))


class World:
    """Один епізод: істота в середовищі, фіксований `DT`, детермінований сід."""

    def __init__(
        self,
        creature_cfg: CreatureConfig,
        env_cfg: EnvironmentConfig,
        genome: np.ndarray | None = None,
        seed: int = 0,
        *,
        controller=None,
        episode_seconds: float = C.GA_EPISODE_SECONDS,
        spawn_index: int = 0,
        weights: FitnessWeights | None = None,
    ):
        self.creature_cfg = creature_cfg
        self.env = env_cfg
        self.genome = genome
        self.seed = int(seed)
        self.episode_seconds = float(episode_seconds)
        # Один генератор на епізод; епізодні сіди похідні від (run, gen, i, e)
        self.rng = np.random.Generator(np.random.PCG64(self.seed))

        self.physics = PhysicsWorld(env_cfg)
        position, angle = self._spawn_pose(spawn_index)
        self.start_position = np.array(position, dtype=np.float64)
        self.creature: Creature = build_creature(creature_cfg, position, angle)
        self.physics.add_creature(self.creature)

        self.n_joints = self.creature.n_joints
        self.sensors = Sensors(self.creature)
        self.brain: Brain | None = None
        if controller is None and genome is not None:
            self.brain = Brain(genome, self.sensors.n_in, self.n_joints)
            controller = BrainController(self.brain)
        self.controller = controller or SineController(creature_cfg)
        self.commands = np.zeros(self.n_joints, dtype=np.float64)
        self._u_abs = 0.0
        self.fitness = FitnessAccumulator(
            self.episode_seconds, self.n_joints, position, weights)

        self.steps = 0
        self.t = 0.0
        self.alive = True
        #: Причина завершення епізоду: `None`, `"time"` (далі — `"caught"`, етап 4)
        self.ended_reason: str | None = None

    # --- старт -------------------------------------------------------------
    def _spawn_pose(self, index: int) -> tuple[tuple[float, float], float]:
        """Старт із `spawns[index]`; порожній список — центр світу (§9.2)."""
        spawns = self.env.spawns
        if not spawns:
            return (0.0, 0.0), 0.0
        spawn = spawns[index % len(spawns)]
        return (float(spawn.position[0]), float(spawn.position[1])), float(spawn.angle)

    # --- крок --------------------------------------------------------------
    def step(self) -> None:
        """Один крок `DT`. Мозок працює рідше — кожні `CONTROL_EVERY` кроків."""
        if not self.alive:
            return
        if self.steps % C.CONTROL_EVERY == 0:
            self.commands = np.clip(
                np.asarray(self.controller(self.t, self), dtype=np.float64), -1.0, 1.0)
            self.creature.set_motor_rates(self.commands)
            self._u_abs = float(np.abs(self.commands).sum())
        self.physics.step(self.t)
        self.steps += 1
        self.t = self.steps * C.DT
        vx, vy = self.creature.body.velocity
        self.fitness.step(math.hypot(vx, vy), self._u_abs)
        if self.t >= self.episode_seconds:
            self.alive = False
            self.ended_reason = "time"

    def run(self, seconds: float | None = None) -> None:
        """Проганяє епізод до кінця (headless-шлях: без рендера й пауз)."""
        limit = self.episode_seconds if seconds is None else seconds
        while self.alive and self.t < limit:
            self.step()

    # --- похідні величини --------------------------------------------------
    @property
    def displacement(self) -> float:
        """Чисте зміщення від старту — те саме, що компонент `D` (§7.1)."""
        pos = np.array(self.creature.body.position, dtype=np.float64)
        return float(np.linalg.norm(pos - self.start_position))

    def fitness_result(self) -> tuple[float, dict[str, float]]:
        """`(F, компоненти)` на поточний момент; після кінця епізоду — підсумок (§7)."""
        pos = self.creature.body.position
        return self.fitness.result(self.t, (pos.x, pos.y))

    @property
    def max_speed(self) -> float:
        return max(float(b.velocity.length) for b in self.creature.bodies)

    def is_finite(self) -> bool:
        """Контроль здоров'я симуляції: жодних NaN/inf у позиціях і швидкостях."""
        values = []
        for body in self.creature.bodies:
            values += [body.position.x, body.position.y, body.velocity.x,
                       body.velocity.y, body.angle, body.angular_velocity]
        return bool(np.all(np.isfinite(values)))

    # --- пакети для /ws/live (§9.5) ---------------------------------------
    def scene_packet(self) -> dict:
        """Статика сцени — надсилається один раз при підключенні (§9.5)."""
        body = self.creature_cfg.body
        return {
            "type": "scene",
            "world": {"width": self.env.world.width, "height": self.env.world.height},
            "obstacles_static": [
                {"id": o.index, "type": o.kind, "vertices": o.vertices,
                 "x": o.position[0], "y": o.position[1], "radius": o.radius}
                for o in self.physics.static_obstacles
            ],
            "obstacles_dynamic": [
                {"id": m.index, "vertices": m.vertices}
                for m in self.physics.moving_obstacles
            ],
            "waypoints": [[list(w) for w in p.waypoints] for p in self.env.predators],
            "creature": {
                "name": self.creature_cfg.name,
                "shape": body.shape,
                "radius": body.radius,
                "vertices": body.vertices,
            },
            "episode_seconds": self.episode_seconds,
        }

    def snapshot(self) -> dict:
        """Кадр `FramePacket` без службових полів транспорту (§9.5).

        Промені й аура порожні до етапу 4 — див. примітку в §9.5.
        """
        body = self.creature.body
        fitness, components = self.fitness_result()
        cfg_body = self.creature_cfg.body
        return {
            "type": "frame",
            "t": round(self.t, 4),
            "creature": {
                "body": {
                    "x": body.position.x, "y": body.position.y, "angle": body.angle,
                    "shape": cfg_body.shape, "radius": cfg_body.radius,
                    "vertices": cfg_body.vertices,
                },
                "segments": [
                    {"limb": s.limb, "idx": s.idx,
                     "x": s.body.position.x, "y": s.body.position.y,
                     "angle": s.body.angle, "length": s.length, "width": s.width}
                    for s in self.creature.segments
                ],
                "joint_outputs": [round(float(u), 4) for u in self.commands],
                "alive": self.alive,
            },
            "predators": [
                {"id": p.index, "enabled": p.cfg.enabled,
                 "x": p.position.x, "y": p.position.y, "angle": p.body.angle,
                 "state": p.state, "r_body": p.cfg.r_body,
                 "r_aura": p.cfg.r_aura, "r_detect": p.cfg.r_detect}
                for p in self.physics.predators
            ],
            "sensors": {"rays": [], "aura": 0.0, "bearing": 0.0},
            "obstacles_dynamic": [
                {"id": m.index, "x": m.body.position.x, "y": m.body.position.y,
                 "angle": m.body.angle}
                for m in self.physics.moving_obstacles
            ],
            "hud": {
                "generation": None,
                "episode_t": round(self.t, 3),
                "displacement": round(self.displacement, 3),
                "speed": round(float(body.velocity.length), 3),
                "fitness_running": round(fitness, 4),
                "components": {k: round(v, 4) for k, v in components.items()},
            },
        }
