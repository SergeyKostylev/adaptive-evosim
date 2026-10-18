"""Збереження прогонів: config.json, append-only metrics.csv,
best_genome.json, чекпоінти (§11). Мінімум — етап 3, повний формат — етап 5.

`metrics.csv` містить лише детерміновану частину `GenerationStats`, тож
однаковий сід дає побітово однаковий файл (R7). Час оцінки залежить від
машини й живе поруч у `timing.csv` (§9.6).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .. import config as C
from ..schemas import ExperimentConfig

METRICS_FIELDS: tuple[str, ...] = (
    "generation", "best_fitness", "avg_fitness", "std_fitness", "diversity",
    *C.FITNESS_KEYS,
)
TIMING_FIELDS: tuple[str, ...] = ("generation", "eval_seconds", "sim_fps_equivalent")

#: `{name}__{YYYYMMDD-HHMM}` з необов'язковим суфіксом `-N` (§11). Ім'я
#: прогону вже обмежене схемою `ExperimentConfig.name`, тож шлях безпечний
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}__\d{8}-\d{4}(-\d+)?$")


class RunNotFound(LookupError):
    pass


def runs_root() -> Path:
    return C.DATA_DIR / C.RUNS_SUBDIR


def archive_root() -> Path:
    return C.DATA_DIR / C.ARCHIVE_SUBDIR


def run_path(run_id: str) -> Path:
    """Каталог прогону; недопустимий `run_id` (зокрема зі шляхом) — `RunNotFound`."""
    if not RUN_ID_RE.match(run_id or ""):
        raise RunNotFound(f"недопустимий run_id «{run_id}»")
    root = runs_root().resolve()
    path = (root / run_id).resolve()
    if path.parent != root:
        raise RunNotFound(f"недопустимий run_id «{run_id}»")
    return path


def _fmt(value) -> str:
    """Числа — найкоротшим точним записом (`repr`): той самий float дає ті самі байти."""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _append_row(path: Path, fields: tuple[str, ...], row: dict) -> None:
    """Один рядок у кінець CSV, заголовок — якщо файл новий. `fsync` — щоб
    аварійне вбивство процесу не лишило напівзаписаного покоління (§11)."""
    new = not path.exists()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if new:
        writer.writerow(fields)
    writer.writerow([_fmt(row[f]) for f in fields])
    with path.open("a", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
        fh.flush()
        os.fsync(fh.fileno())


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class RunStore:
    """Каталог одного прогону `experiments/runs/{run_id}/`."""

    def __init__(self, path: Path):
        self.path = path

    @property
    def run_id(self) -> str:
        return self.path.name

    @classmethod
    def create(cls, cfg: ExperimentConfig, now: datetime | None = None) -> "RunStore":
        """Новий прогін: свіжий `run_id`, знімок конфігу. Нічого не перезаписує —
        два старти за одну хвилину отримують суфікс `-2`, `-3`, … (§11)."""
        stamp = (now or datetime.now()).strftime(C.RUN_ID_TIME_FMT)
        base = f"{cfg.name}__{stamp}"
        root = runs_root()
        root.mkdir(parents=True, exist_ok=True)
        n = 1
        while True:
            run_id = base if n == 1 else f"{base}-{n}"
            path = run_path(run_id)
            try:
                path.mkdir()
                break
            except FileExistsError:
                n += 1
        store = cls(path)
        _atomic_write(path / "config.json",
                      json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return store

    @classmethod
    def open(cls, run_id: str) -> "RunStore":
        path = run_path(run_id)
        if not (path / "config.json").exists():
            raise RunNotFound(f"прогін «{run_id}» не знайдено")
        return cls(path)

    # --- запис -------------------------------------------------------------
    def append_generation(self, stats: dict) -> None:
        """Рядок покоління: детерміновані поля — у `metrics.csv`, час — у `timing.csv`."""
        row = {**stats, **stats["best_components"]}
        _append_row(self.path / "metrics.csv", METRICS_FIELDS, row)
        _append_row(self.path / "timing.csv", TIMING_FIELDS, row)

    def save_best_genome(self, genome_json: dict) -> None:
        _atomic_write(self.path / "best_genome.json", json.dumps(genome_json))

    def archive(self) -> Path:
        """Копія завершеного прогону в `archive/{run_id}/` (§11).

        Збирається в тимчасовому каталозі й переіменовується одним кроком: в
        архіві не буває напівскопійованого прогону. Повторний виклик нічого
        не перезаписує.
        """
        root = archive_root()
        target = root / self.run_id
        if target.exists():
            return target
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{self.run_id}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        for name in C.ARCHIVE_FILES:
            src = self.path / name
            if src.exists():
                shutil.copy2(src, staging / name)
        os.replace(staging, target)
        return target

    # --- читання -----------------------------------------------------------
    def load_config(self) -> ExperimentConfig:
        return ExperimentConfig.model_validate_json(
            (self.path / "config.json").read_text(encoding="utf-8"))

    def load_best_genome(self) -> dict | None:
        path = self.path / "best_genome.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_generations(self, from_gen: int = 0) -> list[dict]:
        """Рядки `GenerationStats` (§9.6), склеєні з `metrics.csv` і `timing.csv`."""
        timing = {r["generation"]: r for r in _read_rows(self.path / "timing.csv")}
        rows = []
        for r in _read_rows(self.path / "metrics.csv"):
            gen = int(r["generation"])
            if gen < from_gen:
                continue
            t = timing.get(r["generation"], {})
            rows.append({
                "type": "generation",
                "run_id": self.run_id,
                "generation": gen,
                "best_fitness": float(r["best_fitness"]),
                "avg_fitness": float(r["avg_fitness"]),
                "std_fitness": float(r["std_fitness"]),
                "diversity": float(r["diversity"]),
                "best_components": {k: float(r[k]) for k in C.FITNESS_KEYS},
                "eval_seconds": float(t.get("eval_seconds", 0.0)),
                "sim_fps_equivalent": float(t.get("sim_fps_equivalent", 0.0)),
            })
        return rows
