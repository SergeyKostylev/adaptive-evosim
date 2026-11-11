"""Збереження прогонів: config.json, append-only metrics.csv,
best_genome.json, чекпоінти ГА, список прогонів (§11).

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

import numpy as np

from .. import config as C
from ..schemas import ExperimentConfig

#: Частки спійманих з'явились на етапі 4 — у кінці рядка, щоб старші
#: `metrics.csv` лишались префіксом формату (§9.6)
CAUGHT_FIELDS: tuple[str, ...] = ("caught_top10", "caught_all")
METRICS_FIELDS: tuple[str, ...] = (
    "generation", "best_fitness", "avg_fitness", "std_fitness", "diversity",
    *C.FITNESS_KEYS, *CAUGHT_FIELDS,
)
TIMING_FIELDS: tuple[str, ...] = ("generation", "eval_seconds", "sim_fps_equivalent")

#: `{name}__{YYYYMMDD-HHMM}` з необов'язковим суфіксом `-N` (§11). Ім'я
#: прогону вже обмежене схемою `ExperimentConfig.name`, тож шлях безпечний
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}__\d{8}-\d{4}(-\d+)?$")
CHECKPOINT_RE = re.compile(r"^gen_(\d+)\.npz$")


class RunNotFound(LookupError):
    pass


def runs_root() -> Path:
    return C.DATA_DIR / C.RUNS_SUBDIR


def archive_root() -> Path:
    return C.DATA_DIR / C.ARCHIVE_SUBDIR


def _checked_path(root: Path, run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise RunNotFound(f"недопустимий run_id «{run_id}»")
    root = root.resolve()
    path = (root / run_id).resolve()
    if path.parent != root:
        raise RunNotFound(f"недопустимий run_id «{run_id}»")
    return path


def run_path(run_id: str) -> Path:
    """Каталог прогону; недопустимий `run_id` (зокрема зі шляхом) — `RunNotFound`."""
    return _checked_path(runs_root(), run_id)


def archive_path(run_id: str) -> Path:
    return _checked_path(archive_root(), run_id)


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
    """Каталог одного прогону: `experiments/runs/{run_id}/` або, для прогону,
    що лишився тільки в архіві, `experiments/archive/{run_id}/` (лише читання й
    експорт — чекпоінтів там немає)."""

    def __init__(self, path: Path, location: str = "runs"):
        self.path = path
        self.location = location

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
    def open(cls, run_id: str, archive: bool = True) -> "RunStore":
        """Локальна копія має пріоритет; `archive=False` — лише `runs/`
        (продовження пише в каталог, тож архівна копія не годиться)."""
        path = run_path(run_id)
        if (path / "config.json").exists():
            return cls(path)
        if archive:
            path = archive_path(run_id)
            if (path / "config.json").exists():
                return cls(path, "archive")
        raise RunNotFound(f"прогін «{run_id}» не знайдено")

    # --- запис -------------------------------------------------------------
    def append_generation(self, stats: dict) -> None:
        """Рядок покоління: детерміновані поля — у `metrics.csv`, час — у `timing.csv`."""
        row = {**stats, **stats["best_components"]}
        _append_row(self.path / "metrics.csv", METRICS_FIELDS, row)
        _append_row(self.path / "timing.csv", TIMING_FIELDS, row)

    def save_best_genome(self, genome_json: dict) -> None:
        _atomic_write(self.path / "best_genome.json", json.dumps(genome_json))

    def save_checkpoint(self, state: dict) -> Path:
        """`checkpoints/gen_{NNN}.npz` — стан ГА після покоління `NNN` (§11).

        Атомарно: tmp + `fsync` + `os.replace`, тож аварійне вбивство лишає або
        попередній чекпоінт, або новий, але не напівзаписаний.
        """
        folder = self.path / C.CHECKPOINTS_SUBDIR
        folder.mkdir(exist_ok=True)
        target = folder / f"gen_{state['generation']:03d}.npz"
        best = state["best_genome"]
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as fh:
            np.savez(
                fh,
                population=np.asarray(state["population"], dtype=np.float32),
                rng_state=np.array(json.dumps(state["rng_state"])),
                generation=np.int64(state["generation"]),
                best_fitness=np.float64(state["best_fitness"]),
                best_genome=(np.zeros(0, dtype=np.float32) if best is None
                             else np.asarray(best, dtype=np.float32)),
                best_generation=np.int64(state["best_generation"]),
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        return target

    def checkpoints(self) -> list[int]:
        """Номери поколінь наявних чекпоінтів, за зростанням."""
        folder = self.path / C.CHECKPOINTS_SUBDIR
        if not folder.is_dir():
            return []
        gens = (CHECKPOINT_RE.match(p.name) for p in folder.iterdir())
        return sorted(int(m.group(1)) for m in gens if m)

    def load_checkpoint(self, generation: int | None = None) -> dict | None:
        """Чекпоінт покоління `generation` (дефолт — останній) або `None`."""
        gens = self.checkpoints()
        if not gens:
            return None
        gen = gens[-1] if generation is None else generation
        with np.load(self.path / C.CHECKPOINTS_SUBDIR / f"gen_{gen:03d}.npz") as data:
            best = data["best_genome"]
            return {
                "population": data["population"].copy(),
                "rng_state": json.loads(str(data["rng_state"])),
                "generation": int(data["generation"]),
                "best_fitness": float(data["best_fitness"]),
                "best_genome": best.copy() if best.size else None,
                "best_generation": int(data["best_generation"]),
            }

    def truncate_to(self, generation: int) -> None:
        """Лишити в `metrics.csv`/`timing.csv` рядки поколінь `≤ generation`.

        Для продовження з чекпоінта: пізніші покоління буде пораховано заново
        (побітово так само). Решта байтів рядків не чіпається — перезапис
        лише відкидає хвіст, тож префікс файлу лишається тим самим.
        """
        for name in ("metrics.csv", "timing.csv"):
            path = self.path / name
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            keep = lines[:1] + [ln for ln in lines[1:]
                                if ln.strip() and int(ln.split(",", 1)[0]) <= generation]
            # Обірваний останній рядок (kill посеред запису) теж відкидається
            keep = [ln for ln in keep if ln.endswith("\n")]
            _atomic_write(path, "".join(keep))

    def save_config(self, cfg: ExperimentConfig) -> None:
        _atomic_write(self.path / "config.json",
                      json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False, indent=2))

    def archive(self, replace: bool = False) -> Path:
        """Копія прогону в `archive/{run_id}/` (§11).

        Збирається в тимчасовому каталозі й переіменовується одним кроком: в
        архіві не буває напівскопійованого прогону. Наявна копія не
        перезаписується, окрім `replace=True` — подовжений прогін є її
        надмножиною; тоді стара копія спершу відсувається, а видаляється
        лише після того, як нова стала на місце.
        """
        root = archive_root()
        target = root / self.run_id
        if target.exists() and not replace:
            return target
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{self.run_id}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        for name in C.ARCHIVE_FILES:
            src = self.path / name
            if src.exists():
                shutil.copy2(src, staging / name)
        old = None
        if target.exists():
            old = root / f".{self.run_id}.old"
            shutil.rmtree(old, ignore_errors=True)
            os.replace(target, old)
        os.replace(staging, target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
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

    def summary(self) -> dict:
        """Рядок `GET /api/experiments` без `state` (його знає лише менеджер)."""
        cfg = json.loads((self.path / "config.json").read_text(encoding="utf-8"))
        rows = _read_rows(self.path / "metrics.csv")
        best = max(rows, key=lambda r: float(r["best_fitness"]), default=None)
        stamp = self.run_id.split("__")[-1][:13]           # YYYYMMDD-HHMM без суфікса -N
        gens = self.checkpoints()
        return {
            "run_id": self.run_id,
            "name": cfg.get("name", self.run_id.split("__")[0]),
            "created_at": datetime.strptime(stamp, C.RUN_ID_TIME_FMT).isoformat(),
            "generations_done": int(rows[-1]["generation"]) if rows else 0,
            "generations_total": int(cfg.get("ga", {}).get("generations", C.GA_GENERATIONS)),
            "best_fitness": float(best["best_fitness"]) if best else None,
            "best_generation": int(best["generation"]) if best else None,
            "archived": (archive_root() / self.run_id / "config.json").exists(),
            "last_checkpoint": gens[-1] if gens else None,
            "location": self.location,
        }

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
                # У прогонах етапу 3 цих колонок немає — невідомо, а не нуль
                **{k: float(r[k]) if r.get(k) not in (None, "") else None
                   for k in CAUGHT_FIELDS},
                "eval_seconds": float(t.get("eval_seconds", 0.0)),
                "sim_fps_equivalent": float(t.get("sim_fps_equivalent", 0.0)),
            })
        return rows


def list_runs() -> list[RunStore]:
    """Усі прогони: локальні з `runs/` і ті, що є лише в архіві; новіші першими."""
    found: dict[str, RunStore] = {}
    for root, location in ((archive_root(), "archive"), (runs_root(), "runs")):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if RUN_ID_RE.match(path.name) and (path / "config.json").exists():
                found[path.name] = RunStore(path, location)    # runs/ перекриває архів
    return sorted(found.values(), key=lambda st: st.run_id.split("__")[-1], reverse=True)
