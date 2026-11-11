"""Вибір чемпіона прогону переоцінкою на однакових тестових сценаріях.

Рекорд прогону (`best_genome.json`) — особина з найвищим фітнесом за весь
прогін. Зі спільними сценаріями покоління порівнюються на **різних** сценаріях,
тож рекорд потрапляє в найлегше покоління (журнал етапу 4, §7). Чемпіон
обирається інакше: кожен кандидат — уся популяція з останнього чекпоінта плюс
рекорд — грає той самий набір тестових епізодів, і перемагає найвищий середній
фітнес на них.

Тестові сценарії — «покоління» `SELECT_GEN, SELECT_GEN+1, …` зі спільними
сценаріями: поза навчальними (≤ 100 000 поколінь) і окремо від сідів абляції
`reaction.py` (`TEST_GEN = 100 000`), щоб абляція не перевіряла чемпіона на тих
самих епізодах, на яких його обрали. Поворот сцени — як у прогоні.

Результат — `champion.json` у каталозі прогону (і в архіві, якщо прогін там є).

Запуск у контейнері бекенда:
`python -m app.export.champion <run_id> [--episodes 40] [--workers N]`
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from ..evolution import genome as G
from ..evolution.evaluator import Evaluator
from ..services.experiment_store import RunNotFound, RunStore, archive_root

SELECT_GEN = 200_000


def select(store: RunStore, episodes: int = 40, workers: int | None = None) -> dict:
    cfg = store.load_config()
    state = store.load_checkpoint()
    if state is None:
        raise ValueError(f"у прогоні {store.run_id} немає чекпоінта — кандидатів нема звідки взяти")
    record = store.load_best_genome()
    candidates = [("population", i) for i in range(len(state["population"]))]
    genomes = list(state["population"])
    if record is not None:
        candidates.append(("record", None))
        genomes.append(G.from_json(record, cfg.creature))
    population = np.stack(genomes).astype(np.float32)

    # Тестовий набір: блоки по E епізодів, спільні для всіх кандидатів
    E = cfg.ga.episodes_per_individual
    blocks = -(-episodes // E)
    test_cfg = cfg.model_copy(update={"ga": cfg.ga.model_copy(update={"shared_scenarios": True})})
    fitness, caught = [], []
    with Evaluator(test_cfg, workers) as ev:
        for k in range(blocks):
            res = ev.evaluate(population, SELECT_GEN + k)
            fitness.append(res.episode_fitness)
            caught.append(res.caught)
    fitness = np.concatenate(fitness, axis=1)[:, :episodes]
    caught = np.concatenate(caught, axis=1)[:, :episodes]
    mean_f = fitness.mean(axis=1)
    best = int(np.argmax(mean_f))

    def row(i: int) -> dict:
        kind, idx = candidates[i]
        return {"source": kind if idx is None else f"{kind}[{idx}]",
                "mean_F": round(float(mean_f[i]), 4),
                "caught": int(caught[i].sum()), "episodes": int(episodes)}

    order = np.argsort(-mean_f, kind="stable")
    report = {
        "run": store.run_id,
        "checkpoint_generation": state["generation"],
        "test": {"generations_from": SELECT_GEN, "episodes": int(episodes),
                 "shared_scenarios": True, "scene_rotation": cfg.ga.scene_rotation},
        "champion": {**row(best), "genome": G.to_json(population[best], cfg.creature)},
        "record": row(len(candidates) - 1) if record is not None else None,
        "record_generation": state["best_generation"],
        "population_mean_F": round(float(mean_f[:len(state["population"])].mean()), 4),
        "top10": [row(int(i)) for i in order[:10]],
    }
    return report


def write(store: RunStore, report: dict) -> list[str]:
    text = json.dumps(report, ensure_ascii=False, indent=1)
    paths = [store.path / "champion.json"]
    archived = archive_root() / store.run_id
    if store.location == "runs" and archived.is_dir():
        paths.append(archived / "champion.json")
    for path in paths:
        path.write_text(text, encoding="utf-8")
    return [str(p) for p in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.export.champion",
                                     description="Чемпіон прогону: переоцінка на однакових тестових сценаріях")
    parser.add_argument("run_id")
    parser.add_argument("--episodes", type=int, default=40)
    parser.add_argument("--workers", type=int, default=None, help="процесів оцінки (0 — auto)")
    args = parser.parse_args(argv)
    try:
        store = RunStore.open(args.run_id)
        report = select(store, args.episodes, args.workers)
    except (RunNotFound, ValueError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 2
    for path in write(store, report):
        print(path)
    ch, rec = report["champion"], report["record"]
    print(f"чемпіон {ch['source']}: F̄ {ch['mean_F']}, спіймано {ch['caught']}/{ch['episodes']}")
    if rec:
        print(f"рекорд (пок. {report['record_generation']}): F̄ {rec['mean_F']}, "
              f"спіймано {rec['caught']}/{rec['episodes']}")
    print(f"середнє популяції: F̄ {report['population_mean_F']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
