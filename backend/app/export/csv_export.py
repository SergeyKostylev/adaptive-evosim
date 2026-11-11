"""Експорт метрик прогону в CSV (§9.4, §11).

`metrics.csv` як є (детерміновані стовпці §9.6) плюс `eval_seconds` і
`sim_fps_equivalent` з `timing.csv` у кінці рядка. Числа — найкоротшим точним
записом, як у самому `metrics.csv`, тож експорт не губить знаків.

Запуск: `python -m app.export.csv_export <run_id> [out.csv]`
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from ..services.experiment_store import (
    METRICS_FIELDS, TIMING_FIELDS, RunNotFound, RunStore, _read_rows,
)

EXTRA_FIELDS: tuple[str, ...] = tuple(f for f in TIMING_FIELDS if f != "generation")


def export_csv(store: RunStore) -> str:
    """CSV одного прогону; у прогонах етапу 3 колонки спійманих порожні."""
    timing = {r["generation"]: r for r in _read_rows(store.path / "timing.csv")}
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow((*METRICS_FIELDS, *EXTRA_FIELDS))
    for row in _read_rows(store.path / "metrics.csv"):
        t = timing.get(row["generation"], {})
        writer.writerow([*(row.get(f) or "" for f in METRICS_FIELDS),
                         *(t.get(f, "") for f in EXTRA_FIELDS)])
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("використання: python -m app.export.csv_export <run_id> [out.csv]", file=sys.stderr)
        return 2
    try:
        store = RunStore.open(args[0])
    except RunNotFound as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 2
    text = export_csv(store)
    if len(args) > 1:
        Path(args[1]).write_text(text, encoding="utf-8")
        print(args[1])
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
