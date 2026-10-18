"""Headless-оцінка популяції: multiprocessing.Pool, по одному World
на геном/епізод (§6.2). Жодного рендера й sleep.

Відтворюваність не залежить від кількості процесів: кожен епізод має власний
сід, похідний від `(run_seed, gen, individual_idx, episode_idx)`, а
результати складаються за індексом особини, а не в порядку готовності.
Тож один воркер і вісім дають побітово той самий масив фітнесів.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time
from dataclasses import dataclass

import numpy as np

from .. import config as C
from ..core.world import World
from ..schemas import CreatureConfig, EnvironmentConfig, ExperimentConfig, FitnessWeights


class EvaluationCancelled(Exception):
    """Покоління перервано зупинкою прогону; його результати не пишуться."""


def episode_seed(run_seed: int, gen: int, individual: int, episode: int) -> int:
    """`SeedSequence(run_seed).spawn(…)[gen].spawn(…)[i].spawn(…)[e]` (§6.2).

    `spawn_key` — рівно той ключ, який дав би ланцюжок `spawn`, тож
    обходимося без побудови проміжних дерев на кожну особину.
    """
    seq = np.random.SeedSequence(run_seed, spawn_key=(gen, individual, episode))
    return int(seq.generate_state(1, dtype=np.uint64)[0])


def resolve_workers(requested: int | None = None) -> int:
    """`0`/`None` — auto: `cpu_count − 1`, але не менше одного (§6.2, §12)."""
    n = C.WORKERS_EVAL if requested is None else requested
    if n <= 0:
        n = (os.cpu_count() or 2) - 1
    return max(1, n)


@dataclass
class _Spec:
    """Незмінне за прогін: що й як оцінюємо. Будується в кожному процесі один раз."""

    creature: CreatureConfig
    environment: EnvironmentConfig
    weights: FitnessWeights
    episodes: int
    episode_seconds: float
    run_seed: int

    @classmethod
    def from_experiment(cls, cfg: ExperimentConfig) -> "_Spec":
        return cls(cfg.creature, cfg.environment, cfg.fitness_weights,
                   cfg.ga.episodes_per_individual, cfg.ga.episode_seconds, cfg.seed)


def evaluate_individual(spec: _Spec, genome: np.ndarray, gen: int, individual: int
                        ) -> tuple[list[float], list[dict[str, float]], int]:
    """Усі епізоди однієї особини → `(F по епізодах, компоненти, кроків)`.

    Епізод `e` стартує з `spawns[e]` (по колу): кілька точок появи — це й є
    різні стартові позиції §7.3.
    """
    fitnesses, components, steps = [], [], 0
    for e in range(spec.episodes):
        world = World(
            spec.creature, spec.environment, genome=genome,
            seed=episode_seed(spec.run_seed, gen, individual, e),
            episode_seconds=spec.episode_seconds, spawn_index=e,
            weights=spec.weights,
        )
        world.run()
        f, comps = world.fitness_result()
        fitnesses.append(f)
        components.append(comps)
        steps += world.steps
    return fitnesses, components, steps


# --- воркер пулу ------------------------------------------------------------
_worker_spec: _Spec | None = None


def _init_worker(cfg_json: str) -> None:
    global _worker_spec
    _worker_spec = _Spec.from_experiment(ExperimentConfig.model_validate_json(cfg_json))


def _work(task: tuple[int, int, np.ndarray]):
    individual, gen, genome = task
    return individual, *evaluate_individual(_worker_spec, genome, gen, individual)


# --- результат покоління ----------------------------------------------------
@dataclass
class EvalResult:
    fitness: np.ndarray                  # [P] — середнє по епізодах (§7.3)
    episode_fitness: np.ndarray          # [P × E]
    components: list[dict[str, float]]   # [P] — середні компоненти
    steps: int                           # усього фізичних кроків покоління
    seconds: float


class Evaluator:
    """Оцінювач одного прогону; пул процесів живе, доки живе оцінювач."""

    def __init__(self, cfg: ExperimentConfig, workers: int | None = None):
        self.cfg = cfg
        self.spec = _Spec.from_experiment(cfg)
        self.workers = resolve_workers(workers)
        self._pool = None

    # Пул піднімається ліниво: старт процесів коштує секунду, а оцінювач
    # з одним воркером (тести, налагодження) його взагалі не потребує
    def _ensure_pool(self):
        if self._pool is None:
            # `spawn` і на Linux: fork із процесу з потоками (uvicorn + ГА)
            # може успадкувати захоплені локи
            ctx = mp.get_context("spawn")
            self._pool = ctx.Pool(self.workers, initializer=_init_worker,
                                  initargs=(self.cfg.model_dump_json(),))
        return self._pool

    def evaluate(self, population: np.ndarray, gen: int,
                 cancel: threading.Event | None = None) -> EvalResult:
        started = time.perf_counter()
        n = len(population)
        E = self.spec.episodes
        episode_fitness = np.zeros((n, E), dtype=np.float64)
        components: list[dict[str, float] | None] = [None] * n
        steps = 0

        def collect(individual, fits, comps, n_steps):
            nonlocal steps
            episode_fitness[individual] = fits
            components[individual] = {k: float(np.mean([c[k] for c in comps]))
                                      for k in C.FITNESS_KEYS}
            steps += n_steps

        if self.workers == 1:
            for i in range(n):
                if cancel is not None and cancel.is_set():
                    raise EvaluationCancelled()
                collect(i, *evaluate_individual(self.spec, population[i], gen, i))
        else:
            pool = self._ensure_pool()
            chunk = max(1, n // (self.workers * C.EVAL_CHUNKS_PER_WORKER))
            tasks = ((i, gen, population[i]) for i in range(n))
            for result in pool.imap_unordered(_work, tasks, chunksize=chunk):
                if cancel is not None and cancel.is_set():
                    self._terminate()
                    raise EvaluationCancelled()
                collect(*result)

        return EvalResult(
            fitness=episode_fitness.mean(axis=1),
            episode_fitness=episode_fitness,
            components=components,
            steps=steps,
            seconds=time.perf_counter() - started,
        )

    def _terminate(self) -> None:
        if self._pool is not None:
            self._pool.terminate()
            self._pool.join()
            self._pool = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool.join()
            self._pool = None

    def __enter__(self) -> "Evaluator":
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is None:
            self.close()
        else:
            self._terminate()
