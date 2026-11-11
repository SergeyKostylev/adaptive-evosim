"""Matplotlib-графіки для дипломної: PNG+PDF, білий фон, підписи
українською, dpi=200 (§11).

Чотири графіки (`C.PLOT_CHARTS`): фітнес, різноманіття, внесок компонентів у
`F` найкращої особини, частки спійманих. Кольори — одна категоріальна палітра
у фіксованому порядку (перевірена на розрізнення при дальтонізмі), ідентичність
серії завжди дублює легенда.

Запуск: `python -m app.export.plots <run_id> [--out DIR]` — пише
`{chart}.png`/`{chart}.pdf` кожного графіка й `figures.pdf` (усі сторінки) у
`figures/` прогону або в `DIR`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import threading
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from .. import config as C
from ..evolution.fitness import TERMS
from ..services.experiment_store import RunNotFound, RunStore

#: Категоріальна палітра (світла тема), слоти в незмінному порядку
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

TITLES = {
    "fitness": "Фітнес по поколіннях",
    "diversity": "Різноманіття популяції",
    "components": "Внесок компонентів у F найкращої особини",
    "caught": "Частка спійманих епізодів",
}
COMPONENT_LABELS = {
    "S": "виживання S", "D": "дистанція D", "G_aura": "небезпека G_aura",
    "C": "колізії C", "Pas": "пасивність Pas", "E_n": "енергія E_n",
}

STYLE = {
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.family": "DejaVu Sans", "font.size": 10,
    "axes.edgecolor": INK_2, "axes.labelcolor": INK, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "xtick.color": INK_2, "ytick.color": INK_2,
    "legend.frameon": False, "legend.fontsize": 9,
    "lines.linewidth": 1.6,
}


#: `rcParams` — глобальний стан matplotlib, а REST рендерить у потоках
_LOCK = threading.Lock()


@contextlib.contextmanager
def _style():
    with _LOCK, matplotlib.rc_context(STYLE):
        yield


def _series(rows: list[dict], key: str) -> np.ndarray:
    return np.array([np.nan if r[key] is None else r[key] for r in rows], dtype=float)


def _moving_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Ковзне середнє «назад» (покоління g — середнє g−w+1…g): без зазирання вперед."""
    out = np.empty_like(values)
    for i in range(len(values)):
        out[i] = np.nanmean(values[max(0, i - window + 1):i + 1])
    return out


def _legend_below(ax, ncol: int) -> None:
    """Легенда під віссю: усередині поля вона накриває дані якогось прогону."""
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=ncol)


def has_caught(rows: list[dict]) -> bool:
    return any(r.get("caught_top10") is not None for r in rows)


def charts_for(rows: list[dict]) -> list[str]:
    """Графіки, які має сенс малювати для цих рядків (у прогонах етапу 3 немає спійманих)."""
    return [c for c in C.PLOT_CHARTS if c != "caught" or has_caught(rows)]


def figure(chart: str, rows: list[dict], weights) -> Figure:
    """Одна фігура `chart` з рядків `GenerationStats` (§9.6). Без pyplot — фігура
    ні в якому глобальному реєстрі не живе; викликати всередині `_style()`."""
    fig = Figure(figsize=(7.0, 3.6), layout="constrained")
    ax = fig.add_subplot()
    gen = np.array([r["generation"] for r in rows])
    ax.set_xlabel("Покоління")
    ax.set_title(TITLES[chart], loc="left")

    if chart == "fitness":
        best, avg, std = (_series(rows, k) for k in ("best_fitness", "avg_fitness", "std_fitness"))
        ax.fill_between(gen, avg - std, avg + std, color=PALETTE[1], alpha=0.18,
                        linewidth=0, label="середній ± σ")
        ax.plot(gen, avg, color=PALETTE[1], label="середній по популяції")
        ax.plot(gen, best, color=PALETTE[0], label="найкращий у поколінні")
        ax.set_ylabel("Фітнес F")
        _legend_below(ax, ncol=3)

    elif chart == "diversity":
        ax.plot(gen, _series(rows, "diversity"), color=PALETTE[0])
        ax.set_ylabel("Різноманіття (сер. L2 / √G)")
        ax.set_ylim(bottom=0)

    elif chart == "components":
        contrib = {}
        for key in C.FITNESS_KEYS:
            field, sign = TERMS[key]
            contrib[key] = sign * getattr(weights, field) * np.array(
                [r["best_components"][key] for r in rows], dtype=float)
        pos = [k for k in C.FITNESS_KEYS if TERMS[k][1] > 0]
        neg = [k for k in C.FITNESS_KEYS if TERMS[k][1] < 0]
        colors = dict(zip(C.FITNESS_KEYS, PALETTE))
        for group in (pos, neg):
            base = np.zeros(len(rows))
            for key in group:
                top = base + contrib[key]
                ax.fill_between(gen, base, top, color=colors[key], alpha=0.85,
                                linewidth=0, label=COMPONENT_LABELS[key])
                base = top
        ax.plot(gen, sum(contrib.values()), color=INK, linewidth=1.2,
                label="сума = найкращий F")
        ax.axhline(0, color=INK_2, linewidth=0.8)
        ax.set_ylabel("Внесок w·компонент")
        ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5))

    elif chart == "caught":
        w = C.PLOT_SMOOTH_WINDOW
        for i, (key, label) in enumerate((("caught_top10", "топ-10 за F"),
                                          ("caught_all", "уся популяція"))):
            values = _series(rows, key) * 100
            ax.plot(gen, values, color=PALETTE[i], alpha=0.3, linewidth=0.8)
            ax.plot(gen, _moving_mean(values, w), color=PALETTE[i],
                    label=f"{label} (ковзне середнє, {w} пок.)")
        ax.set_ylabel("Спіймано, % епізодів")
        ax.set_ylim(0, 100)
        _legend_below(ax, ncol=2)

    else:
        raise ValueError(f"невідомий графік «{chart}»")
    return fig


def render_png(chart: str, rows: list[dict], weights) -> bytes:
    buf = io.BytesIO()
    with _style():
        figure(chart, rows, weights).savefig(buf, format="png", dpi=C.PLOT_DPI)
    return buf.getvalue()


def render_pdf(rows: list[dict], weights, title: str) -> bytes:
    """Багатосторінковий PDF: по графіку на сторінку, векторний."""
    buf = io.BytesIO()
    with _style(), PdfPages(buf, metadata={"Title": title, "Creator": "Adaptive EvoSim"}) as pdf:
        for chart in charts_for(rows):
            pdf.savefig(figure(chart, rows, weights))
    return buf.getvalue()


def write_figures(store: RunStore, out: Path | None = None) -> list[Path]:
    """Усі графіки прогону в PNG і PDF + зведений `figures.pdf` (§11)."""
    rows = store.read_generations()
    if not rows:
        raise ValueError(f"у прогоні {store.run_id} ще немає поколінь")
    weights = store.load_config().fitness_weights
    out = out or store.path / C.FIGURES_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    written = []
    with _style():
        for chart in charts_for(rows):
            fig = figure(chart, rows, weights)
            for ext in ("png", "pdf"):
                path = out / f"{chart}.{ext}"
                fig.savefig(path, dpi=C.PLOT_DPI)
                written.append(path)
    path = out / "figures.pdf"
    path.write_bytes(render_pdf(rows, weights, store.run_id))
    written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.export.plots",
                                     description="Графіки прогону для дипломної (§11)")
    parser.add_argument("run_id")
    parser.add_argument("--out", type=Path, help="куди писати (дефолт — figures/ прогону)")
    args = parser.parse_args(argv)
    try:
        paths = write_figures(RunStore.open(args.run_id), args.out)
    except (RunNotFound, ValueError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
