"""Цикл ГА: турнір, елітизм, uniform crossover, гаусова мутація,
статистика покоління, diversity (§6.2).

Один `np.random.Generator(PCG64(seed))` на прогін, і звертання до нього йдуть
у фіксованому порядку: ініціалізація → (diversity → відтворення) на кожне
покоління. Оцінка генератор не чіпає — епізоди мають власні сіди
(`evaluator.episode_seed`), тож кількість воркерів на результат не впливає.

Стан після покоління (популяція, генератор, рекорд) зберігається чекпоінтом
(§11), і продовження з нього побітово тотожне неперервному прогону.

CLI: `python -m app.evolution.ga --config <json> [--generations N] [--workers N]`
або `--resume <run_id> [--generations N]`.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .. import config as C
from ..schemas import ExperimentConfig
from . import genome as G
from .evaluator import EvaluationCancelled, Evaluator


class GeneticAlgorithm:
    """Стан еволюції одного прогону: популяція, генератор, найкраща особина."""

    def __init__(self, cfg: ExperimentConfig):
        self.cfg = cfg
        self.ga = cfg.ga
        self.creature = cfg.creature
        self.rng = np.random.Generator(np.random.PCG64(cfg.seed))
        self.population = G.init_population(
            self.ga.population, self.creature.n_in, self.creature.n_joints, self.rng)
        #: Номер **наступного** покоління (нумерація з 1, як у критеріях PLAN.md)
        self.generation = 1
        self.best_fitness = -math.inf
        self.best_genome: np.ndarray | None = None
        self.best_generation = 0
        #: Чи оновило останнє покоління рекорд прогону (→ переписати `best_genome.json`)
        self.improved = False
        self.generation_best: np.ndarray | None = None

    # --- чекпоінт (§11) --------------------------------------------------------
    def checkpoint_state(self) -> dict:
        """Стан після останнього завершеного покоління — все, що потрібно, щоб
        продовжити побітово так само. Перерване покоління генератор не чіпало
        (оцінка його не використовує), тож стан після зупинки теж коректний."""
        return {
            "population": self.population,
            "rng_state": self.rng.bit_generator.state,
            "generation": self.generation - 1,
            "best_fitness": self.best_fitness,
            "best_genome": self.best_genome,
            "best_generation": self.best_generation,
        }

    @classmethod
    def from_checkpoint(cls, cfg: ExperimentConfig, state: dict) -> "GeneticAlgorithm":
        ga = cls(cfg)
        expected = (cfg.ga.population, cfg.creature.genome_size)
        if state["population"].shape != expected:
            raise ValueError(f"чекпоінт не відповідає конфігу: популяція "
                             f"{state['population'].shape}, очікувано {expected}")
        ga.population = state["population"].astype(np.float32, copy=True)
        ga.rng.bit_generator.state = state["rng_state"]
        ga.generation = state["generation"] + 1
        ga.best_fitness = state["best_fitness"]
        ga.best_genome = state["best_genome"]
        ga.best_generation = state["best_generation"]
        return ga

    # --- оператори (§6.2) ---------------------------------------------------
    def tournament(self, fitness: np.ndarray) -> int:
        """Турнір розміру `k` з поверненням; нічия — перший у вибірці."""
        picks = self.rng.integers(0, len(fitness), size=self.ga.tournament_k)
        return int(picks[np.argmax(fitness[picks])])

    def reproduce(self, fitness: np.ndarray) -> np.ndarray:
        """Наступна популяція: еліта без змін, решта — турнір → кросовер → мутація."""
        order = np.argsort(-fitness, kind="stable")
        new_pop = [self.population[i].copy() for i in order[:self.ga.elitism]]
        while len(new_pop) < self.ga.population:
            a = self.population[self.tournament(fitness)]
            b = self.population[self.tournament(fitness)]
            if self.rng.random() < self.ga.p_crossover:
                child = G.uniform_crossover(a, b, self.rng)
            else:
                child = a.copy()
            G.mutate(child, self.rng, self.ga.p_mutation, self.ga.mutation_sigma)
            new_pop.append(child)
        return np.stack(new_pop)

    # --- покоління ------------------------------------------------------------
    def step(self, evaluator: Evaluator, cancel: threading.Event | None = None) -> dict:
        """Оцінка → статистика → відтворення. Повертає `GenerationStats` (§9.6)
        без `run_id` і ключа `type` — їх додає той, хто публікує."""
        gen = self.generation
        result = evaluator.evaluate(self.population, gen, cancel)
        fitness = result.fitness
        best = int(np.argmax(fitness))
        order = np.argsort(-fitness, kind="stable")
        top = order[:C.CAUGHT_TOP_K]

        stats = {
            "generation": gen,
            "best_fitness": float(fitness[best]),
            "avg_fitness": float(fitness.mean()),
            "std_fitness": float(fitness.std()),
            "diversity": G.diversity(self.population, self.rng),
            "best_components": result.components[best],
            # Частка спійманих епізодів: серед топ-10 за F і по всій популяції (§9.6)
            "caught_top10": float(result.caught[top].mean()),
            "caught_all": float(result.caught.mean()),
            "eval_seconds": round(result.seconds, 3),
            "sim_fps_equivalent": round(result.steps / result.seconds) if result.seconds > 0 else 0,
        }
        #: Найкращий геном щойно оціненого покоління — для `live_training` (§9.4)
        self.generation_best = self.population[best].copy()
        self.improved = stats["best_fitness"] > self.best_fitness
        if self.improved:
            self.best_fitness = stats["best_fitness"]
            self.best_genome = self.population[best].copy()
            self.best_generation = gen

        self.population = self.reproduce(fitness)
        self.generation += 1
        return stats


def run_loop(
    ga: GeneticAlgorithm,
    evaluator: Evaluator,
    store,
    *,
    until: int,
    before_generation: Callable[[], bool] | None = None,
    on_generation: Callable[[dict], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Покоління до `until` включно. `before_generation() → False` зупиняє цикл
    (там же чекає пауза); запис у сховище — до публікації, щоб клієнт, який
    отримав подію, міг одразу дочитати її з REST.

    Чекпоінт (§11) — кожні `GA_CHECKPOINT_EVERY` поколінь, на `until` і при
    зупинці, завжди після рядка `metrics.csv` того самого покоління.
    """
    saved = -1

    def checkpoint() -> None:
        nonlocal saved
        done = ga.generation - 1
        if store is not None and done >= 1 and done != saved:
            store.save_checkpoint(ga.checkpoint_state())
            saved = done

    try:
        while ga.generation <= until:
            if before_generation is not None and not before_generation():
                checkpoint()
                return
            stats = ga.step(evaluator, cancel)
            if store is not None:
                store.append_generation(stats)
                if ga.improved:
                    store.save_best_genome(G.to_json(ga.best_genome, ga.creature))
                if stats["generation"] % C.GA_CHECKPOINT_EVERY == 0 or stats["generation"] == until:
                    checkpoint()
            if on_generation is not None:
                on_generation(stats)
    except EvaluationCancelled:
        checkpoint()
        raise


def resume(store, generations: int | None = None):
    """Підготувати продовження прогону з останнього чекпоінта (§11).

    Обрізає метрики до покоління чекпоінта, відновлює `best_genome.json`,
    за потреби подовжує `ga.generations` у `config.json`. Повертає
    `(cfg, ga, extended)`; `ValueError` — продовжувати нема з чого або нікуди.
    """
    cfg = store.load_config()
    state = store.load_checkpoint()
    if state is None:
        raise ValueError(f"у прогоні {store.run_id} немає чекпоінтів — продовжити нема з чого")
    total = cfg.ga.generations
    if generations is not None:
        if generations < total:
            raise ValueError(f"поколінь {generations} менше, ніж уже заплановано ({total})")
        total = generations
    if state["generation"] >= total:
        raise ValueError(f"прогін {store.run_id} уже дійшов до покоління {total}; "
                         "щоб іти далі, задайте більше поколінь")
    extended = total > cfg.ga.generations
    if extended:
        cfg = cfg.model_copy(update={"ga": cfg.ga.model_copy(update={"generations": total})})
        store.save_config(cfg)
    ga = GeneticAlgorithm.from_checkpoint(cfg, state)
    store.truncate_to(state["generation"])
    if ga.best_genome is not None:
        store.save_best_genome(G.to_json(ga.best_genome, ga.creature))
    return cfg, ga, extended


# --- CLI ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    from ..services.experiment_store import RunNotFound, RunStore

    parser = argparse.ArgumentParser(prog="python -m app.evolution.ga",
                                     description="Headless-еволюція за ExperimentConfig (§9.3)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="JSON з ExperimentConfig")
    source.add_argument("--resume", metavar="RUN_ID",
                        help="продовжити прогін з runs/ з останнього чекпоінта (§11)")
    parser.add_argument("--generations", type=int,
                        help="перекриває ga.generations (з --resume — подовжує прогін)")
    parser.add_argument("--workers", type=int, default=None,
                        help="процесів оцінки (0 — auto, 1 — без пулу)")
    args = parser.parse_args(argv)

    extended = False
    if args.resume:
        try:
            store = RunStore.open(args.resume, archive=False)
            cfg, ga, extended = resume(store, args.generations)
        except (RunNotFound, ValueError) as exc:
            print(f"помилка: {exc}", file=sys.stderr)
            return 2
        print(f"продовження {store.run_id} з покоління {ga.generation} до {cfg.ga.generations}",
              flush=True)
    else:
        cfg = ExperimentConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        if args.generations:
            cfg = cfg.model_copy(update={"ga": cfg.ga.model_copy(update={"generations": args.generations})})
        store = RunStore.create(cfg)
        ga = GeneticAlgorithm(cfg)
        print(f"прогін {store.run_id}: P={cfg.ga.population}, поколінь {cfg.ga.generations}, "
              f"n_in={cfg.creature.n_in}, геном {cfg.creature.genome_size}", flush=True)

    def report(s: dict) -> None:
        comps = " ".join(f"{k}={v:.3f}" for k, v in s["best_components"].items()
                         if k in C.FITNESS_COMPONENTS)
        print(f"gen {s['generation']:4d}  best {s['best_fitness']:+.4f}  "
              f"avg {s['avg_fitness']:+.4f}  div {s['diversity']:.3f}  "
              f"спійм. топ-10 {s['caught_top10']:.0%} усі {s['caught_all']:.0%}  [{comps}]  "
              f"{s['eval_seconds']:.1f} с, {s['sim_fps_equivalent']:,} кр/с", flush=True)

    with Evaluator(cfg, args.workers) as evaluator:
        run_loop(ga, evaluator, store, until=cfg.ga.generations, on_generation=report)
    archived = store.archive(replace=extended)   # сюди доходить лише завершений прогін (§11)
    print(f"готово: найкращий F={ga.best_fitness:.4f} (покоління {ga.best_generation}), "
          f"{store.path}; архів — {archived}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
