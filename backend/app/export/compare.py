"""Порівняння груп прогонів на одному рисунку (для тексту дипломної).

Група — кілька прогонів однієї постановки з різними сідами. Для кожного
прогону метрика згладжується ковзним середнім «назад» (`C.PLOT_SMOOTH_WINDOW`),
далі по групі малюється середнє (лінія) і розкид min–max між сідами (смуга);
група з одного прогону — просто лінія. Стиль — той самий, що в `plots.py`.

Запуск у контейнері бекенда:
`python -m app.export.compare --out DIR [--name stage4] [--metric caught_top10 …]
    "v2, поворот=run_a,run_b,run_c" "swimmer-v1, поворот=run_d"`

Пише `{name}_{metric}.png` і `.pdf` на кожну метрику.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from .. import config as C
from ..services.experiment_store import RunNotFound, RunStore
from .plots import PALETTE, _legend_below, _moving_mean, _series, _style

METRICS = {
    "caught_top10": ("Спіймано, % епізодів", 100.0),
    "caught_all": ("Спіймано, % епізодів", 100.0),
    "best_fitness": ("Найкращий фітнес F", 1.0),
}
TITLES = {
    "caught_top10": "Частка спійманих серед 10 найкращих",
    "caught_all": "Частка спійманих серед усієї популяції",
    "best_fitness": "Найкращий фітнес у поколінні",
}


def parse_group(text: str) -> tuple[str, list[str]]:
    label, _, runs = text.rpartition("=")
    if not label or not runs:
        raise ValueError(f"група «{text}»: очікувано «підпис=run_id,run_id,…»")
    return label, [r.strip() for r in runs.split(",") if r.strip()]


def group_curves(run_ids: list[str], metric: str) -> tuple[np.ndarray, np.ndarray]:
    """`(покоління, [прогін × покоління])` — згладжені криві, обрізані до найкоротшого."""
    scale = METRICS[metric][1]
    curves = []
    for run_id in run_ids:
        rows = RunStore.open(run_id).read_generations()
        if not rows or rows[0].get(metric, 0) is None:
            raise ValueError(f"у прогоні {run_id} немає метрики {metric}")
        curves.append(_moving_mean(_series(rows, metric) * scale, C.PLOT_SMOOTH_WINDOW))
    n = min(len(c) for c in curves)
    return np.arange(1, n + 1), np.stack([c[:n] for c in curves])


def figure(groups: list[tuple[str, list[str]]], metric: str) -> Figure:
    fig = Figure(figsize=(7.0, 3.8), layout="constrained")
    ax = fig.add_subplot()
    ax.set_title(TITLES[metric], loc="left")
    ax.set_xlabel("Покоління")
    ax.set_ylabel(METRICS[metric][0])
    for i, (label, run_ids) in enumerate(groups):
        gen, curves = group_curves(run_ids, metric)
        color = PALETTE[i]
        if len(run_ids) > 1:
            ax.fill_between(gen, curves.min(axis=0), curves.max(axis=0), color=color,
                            alpha=0.18, linewidth=0)
            label = f"{label} — середнє {len(run_ids)} сідів, смуга min–max"
        ax.plot(gen, curves.mean(axis=0), color=color, label=label)
    if METRICS[metric][1] == 100.0:
        ax.set_ylim(0, 100)
    ax.text(1.0, 1.01, f"ковзне середнє {C.PLOT_SMOOTH_WINDOW} поколінь", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=8, color="#52514e")
    _legend_below(ax, ncol=1)
    return fig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.export.compare",
                                     description="Групи прогонів на одному рисунку")
    parser.add_argument("groups", nargs="+", help="«підпис=run_id,run_id,…»")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="compare")
    parser.add_argument("--metric", action="append", choices=sorted(METRICS),
                        help="метрика (можна кілька); дефолт — caught_top10 і caught_all")
    args = parser.parse_args(argv)
    metrics = args.metric or ["caught_top10", "caught_all"]
    try:
        groups = [parse_group(g) for g in args.groups]
        args.out.mkdir(parents=True, exist_ok=True)
        with _style():
            for metric in metrics:
                fig = figure(groups, metric)
                for ext in ("png", "pdf"):
                    path = args.out / f"{args.name}_{metric}.{ext}"
                    fig.savefig(path, dpi=C.PLOT_DPI)
                    print(path)
    except (RunNotFound, ValueError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
