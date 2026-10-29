"""Аналіз реакції на хижака (DoD етапу 4, п.3).

Найкращий геном прогону грає N тестових епізодів (сіди поза навчальними) у двох
варіантах: «зрячий» і «сліпий» — той самий мозок, але предаторні канали радара
та аура-сенсор обнулені. До появи хижака в сенсорах обидва варіанти рухаються
побітово однаково (той самий сід → ті самі фази патруля), тож розбіжність після
появи — це реакція саме на хижака.

Результат — `reaction.json` поруч із прогоном в архіві (журнал: `docs/thesis/stage4-experiments.md`).

Запуск у контейнері бекенда: `python -m app.export.reaction <run_dir> [N=40] [out.json]`
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

from app import config as C
from app.core.world import World
from app.evolution import genome as G
from app.evolution.evaluator import episode_seed
from app.schemas import ExperimentConfig

SENSE_R = C.R_RAY                 # хижак «з'явився»: увійшов у дальність променів
TEST_GEN = 100_000                # сіди поза навчальними поколіннями
PRED_IDX = [2 * k + 1 for k in range(C.N_RAYS)] + [2 * C.N_RAYS + j for j in range(C.N_AURA)]


class Blind:
    def __init__(self, brain):
        self.brain = brain

    def __call__(self, t, world):
        x = np.array(world.inputs, dtype=np.float64)
        x[PRED_IDX] = 0.0
        return self.brain.forward(x)


def play(cfg, genome, seed, spawn, blind):
    w = World(cfg.creature, cfg.environment, genome, seed,
              episode_seconds=cfg.ga.episode_seconds, spawn_index=spawn,
              weights=cfg.fitness_weights)
    if blind:
        w.controller = Blind(w.brain)
    log = []                          # (t, x, y, [(px, py, state)…]) на control-тиках
    while w.alive and w.t < cfg.ga.episode_seconds:
        if w.steps % C.CONTROL_EVERY == 0:
            p = w.creature.body.position
            log.append((w.t, p.x, p.y, [(q.position.x, q.position.y, q.state) for q in w.predators]))
        w.step()
    return w, log


def pos_at(log, t):
    i = min(range(len(log)), key=lambda k: abs(log[k][0] - t))
    return np.array(log[i][1:3]), log[i]


def appearances(log):
    """Моменти, коли якийсь хижак входить у дальність сенсорів (не на старті)."""
    out = []
    n = len(log[0][3])
    for j in range(n):
        inside = None
        for t, x, y, preds in log:
            d = math.hypot(preds[j][0] - x, preds[j][1] - y)
            now = d < SENSE_R
            if inside is False and now:
                out.append((t, j))
            inside = now
    return sorted(out)


def heading(v):
    return math.atan2(v[1], v[0])


def analyse(log, t0, j):
    c0, rec = pos_at(log, t0)
    pred = np.array(rec[3][j][:2])
    away = c0 - pred
    away /= np.linalg.norm(away) + 1e-12
    before = c0 - pos_at(log, t0 - 1.0)[0]
    after = pos_at(log, t0 + 2.0)[0] - c0
    if np.linalg.norm(before) < 0.05 or np.linalg.norm(after) < 0.05:
        return None
    dh = (heading(after) - heading(before) + math.pi) % (2 * math.pi) - math.pi
    cos_b = float(before @ away / np.linalg.norm(before))
    cos_a = float(after @ away / np.linalg.norm(after))
    return {"t": round(t0, 2), "pred": j, "turn_deg": round(math.degrees(dh), 1),
            "cos_away_before": round(cos_b, 3), "cos_away_after": round(cos_a, 3)}


def main():
    run = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    cfg = ExperimentConfig.model_validate_json((run / "config.json").read_text())
    gj = json.loads((run / "best_genome.json").read_text())
    genome = G.from_json(gj, cfg.creature)
    n_sp = max(1, len(cfg.environment.spawns))

    episodes = []
    for e in range(n):
        seed = episode_seed(cfg.seed, TEST_GEN, 0, e)
        res = {}
        for blind in (False, True):
            w, log = play(cfg, genome, seed, e % n_sp, blind)
            ev = [a for a in (analyse(log, t, j) for t, j in appearances(log)) if a]
            res["blind" if blind else "seeing"] = {
                "caught": w.ended_reason == "caught", "t_end": round(w.t, 2),
                "F": round(w.fitness_result()[0], 3), "events": ev,
                "start_in_range": any(math.hypot(p[0] - log[0][1], p[1] - log[0][2]) < SENSE_R
                                      for p in log[0][3]),
                "track": [(round(t, 2), round(x, 3), round(y, 3)) for t, x, y, _ in log[::4]],
            }
        episodes.append({"episode": e, "seed": seed, "spawn": e % n_sp, **res})

    def summary(key):
        eps = [ep[key] for ep in episodes]
        ev = [x for ep in eps for x in ep["events"]]
        turned = [x for x in ev if abs(x["turn_deg"]) > 30 and x["cos_away_after"] > x["cos_away_before"]]
        return {
            "caught": sum(ep["caught"] for ep in eps), "episodes": len(eps),
            "mean_F": round(float(np.mean([ep["F"] for ep in eps])), 3),
            "mean_t_end": round(float(np.mean([ep["t_end"] for ep in eps])), 2),
            "events": len(ev),
            "turn_away_events": len(turned),
            "episodes_with_turn_away": sum(any(abs(x["turn_deg"]) > 30 and x["cos_away_after"] > x["cos_away_before"]
                                               for x in ep["events"]) for ep in eps),
            "mean_cos_away_before": round(float(np.mean([x["cos_away_before"] for x in ev])), 3) if ev else None,
            "mean_cos_away_after": round(float(np.mean([x["cos_away_after"] for x in ev])), 3) if ev else None,
        }

    report = {"run": run.name,
              "seeing": summary("seeing"), "blind": summary("blind"), "episodes": episodes}
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else run / "reaction.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report[k] for k in ("run", "seeing", "blind")},
                     ensure_ascii=False, indent=1))
    print("епізоди зі зрячим поворотом від хижака:",
          [(ep["episode"], ep["spawn"], [x["t"] for x in ep["seeing"]["events"]
            if abs(x["turn_deg"]) > 30 and x["cos_away_after"] > x["cos_away_before"]])
           for ep in episodes if any(abs(x["turn_deg"]) > 30 and x["cos_away_after"] > x["cos_away_before"]
                                      for x in ep["seeing"]["events"])])


if __name__ == "__main__":
    main()
