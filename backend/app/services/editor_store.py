"""Збереження стану конструктора у проєкті (`experiments/editor/`).

Призначення: користувач перезавантажує сторінку — і бачить ту саму сцену.
Зберігається «як є», без валідації проти §9.1–9.3: чернетка може бути
незавершеною (наприклад, істота ще без кінцівок).

Запис атомарний (tmp + `os.replace`), тому обрив під час збереження не
псує попередній файл.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import config as C

STATE_FILENAME = "last_session.json"
PRESETS_SUBDIR = "presets"          # цілі сцени
CREATURES_SUBDIR = "creatures"      # бібліотека істот (лише морфологія)
PREDATORS_SUBDIR = "predators"      # бібліотека хижаків (параметри без маршруту)
MAX_STATE_BYTES = 1 * 1024 * 1024      # 1 МіБ — захист від випадкового «сміття»
MAX_PRESETS = 50                       # щоб каталог не розростався безмежно
MAX_NAME_LEN = 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(target: Path, data: dict) -> None:
    """tmp + os.replace: обрив під час запису не псує попередній файл."""
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def editor_dir() -> Path:
    return C.DATA_DIR / C.EDITOR_SUBDIR


def state_path() -> Path:
    return editor_dir() / STATE_FILENAME


# --- Іменовані варіанти (presets) ------------------------------------------
class InvalidPresetName(ValueError):
    """Назва варіанта не може бути перетворена на безпечне ім'я файлу."""


def presets_dir() -> Path:
    return editor_dir() / PRESETS_SUBDIR


def creatures_dir() -> Path:
    return editor_dir() / CREATURES_SUBDIR


def predators_dir() -> Path:
    return editor_dir() / PREDATORS_SUBDIR


def collection_dir(kind: str) -> Path:
    dirs = {
        "creatures": creatures_dir,
        "predators": predators_dir,
        "presets": presets_dir,
    }
    if kind not in dirs:
        raise ValueError(f"невідома колекція: {kind}")
    return dirs[kind]()


def item_path(kind: str, name: str) -> Path:
    """Те саме, що `preset_path`, але для довільної колекції."""
    return _safe_path(collection_dir(kind), name)


def preset_path(name: str) -> Path:
    return _safe_path(presets_dir(), name)


def _safe_path(base_dir: Path, name: str) -> Path:
    """Шлях до файлу колекції з перевіркою назви та виходу за межі каталогу.

    Назва користувача стає іменем файлу, тому перевірка сувора: ніяких
    розділювачів шляху, керуючих символів, `.`/`..` та прихованих файлів.
    Після складання шляху додатково перевіряємо, що він лежить у `base_dir` —
    захист від будь-якого несподіваного способу обійти перші перевірки.
    """
    raw = name.strip()
    if not raw or len(raw) > MAX_NAME_LEN:
        raise InvalidPresetName(f"Назва має містити 1–{MAX_NAME_LEN} символів")
    if raw in {".", ".."} or raw.startswith("."):
        raise InvalidPresetName("Назва не може починатися з точки")
    if any(ch in raw for ch in ("/", "\\", "\0")):
        raise InvalidPresetName("Назва не може містити / або \\")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InvalidPresetName("Назва містить керуючі символи")
    if raw.endswith("."):
        raise InvalidPresetName("Назва не може закінчуватися точкою")

    base = base_dir.resolve()
    target = (base / f"{raw}.json").resolve()
    if target.parent != base:
        raise InvalidPresetName("Недопустима назва варіанта")
    return target


def _preset_meta(name: str, data: dict, path: Path) -> dict:
    """Короткий опис варіанта для списку в UI (дані — чернетка, тож обережно)."""
    creature = data.get("creature") if isinstance(data.get("creature"), dict) else {}
    env = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    limbs = creature.get("limbs") if isinstance(creature.get("limbs"), list) else []
    obstacles = env.get("obstacles") if isinstance(env.get("obstacles"), list) else []
    predators = env.get("predators") if isinstance(env.get("predators"), list) else []
    legacy = env.get("predator") if isinstance(env.get("predator"), dict) else {}
    waypoints = [
        wp for p in ([*predators, legacy] if legacy else predators)
        if isinstance(p, dict)
        for wp in (p.get("waypoints") or [])
    ]
    segments = sum(
        len(limb.get("segments", [])) for limb in limbs if isinstance(limb, dict)
    )
    return {
        "name": name,
        "saved_at": data.get("saved_at"),
        "creature_name": creature.get("name"),
        "limbs": len(limbs),
        "segments": segments,
        "obstacles": len(obstacles),
        "waypoints": len(waypoints),
        "bytes": path.stat().st_size,
        # Сцена віддається цілком: вона мала, а фронтенду потрібна для
        # мініатюри у списку вибору (інакше там були б самі назви)
        "environment": env or None,
    }


def _creature_meta(name: str, data: dict, path: Path) -> dict:
    """Опис істоти з бібліотеки: морфологія віддається повністю — вона мала,
    а фронтенду вона потрібна для мініатюри у списку."""
    creature = data.get("creature") if isinstance(data.get("creature"), dict) else {}
    limbs = creature.get("limbs") if isinstance(creature.get("limbs"), list) else []
    body = creature.get("body") if isinstance(creature.get("body"), dict) else {}
    return {
        "name": name,
        "saved_at": data.get("saved_at"),
        "creature_name": creature.get("name"),
        "limbs": len(limbs),
        "segments": sum(len(l.get("segments", [])) for l in limbs if isinstance(l, dict)),
        "shape": body.get("shape"),
        "bytes": path.stat().st_size,
        "creature": creature or None,
    }


def _predator_meta(name: str, data: dict, path: Path) -> dict:
    """Опис хижака для списку; параметри віддаються повністю — для прев'ю."""
    pred = data.get("predator") if isinstance(data.get("predator"), dict) else {}
    return {
        "name": name,
        "saved_at": data.get("saved_at"),
        "r_body": pred.get("r_body", C.R_PREDATOR_BODY),
        "r_aura": pred.get("r_aura", C.R_AURA),
        "r_detect": pred.get("r_detect", C.R_DETECT),
        "v_chase": pred.get("v_chase", C.V_CHASE),
        "bytes": path.stat().st_size,
        "predator": pred or None,
    }


_META_BUILDERS = {
    "creatures": _creature_meta,
    "predators": _predator_meta,
}


def list_items(kind: str) -> list[dict]:
    """Елементи колекції, найсвіжіші першими."""
    base = collection_dir(kind)
    if not base.is_dir():
        return []
    out: list[dict] = []
    for path in base.glob("*.json"):
        if path.name.startswith("."):
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(_META_BUILDERS.get(kind, _preset_meta)(path.stem, data, path))
    out.sort(key=lambda m: (m["saved_at"] or "", m["name"]), reverse=True)
    return out


def list_presets() -> list[dict]:
    return list_items("presets")


def save_item(kind: str, name: str, payload: dict) -> dict:
    """Зберігає елемент колекції; перезапис однойменного дозволений."""
    target = item_path(kind, name)
    if not target.exists() and len(list_items(kind)) >= MAX_PRESETS:
        raise ValueError(f"Досягнуто ліміту ({MAX_PRESETS})")
    data = {**payload, "saved_at": _now()}
    _atomic_write(target, data)
    return _META_BUILDERS.get(kind, _preset_meta)(target.stem, data, target)


def load_item(kind: str, name: str) -> dict | None:
    target = item_path(kind, name)
    if not target.is_file():
        return None
    try:
        with target.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def delete_item(kind: str, name: str) -> bool:
    target = item_path(kind, name)
    if not target.is_file():
        return False
    target.unlink()
    return True


def save_preset(name: str, payload: dict) -> dict:
    return save_item("presets", name, payload)


def load_preset(name: str) -> dict | None:
    target = preset_path(name)
    if not target.is_file():
        return None
    try:
        with target.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def delete_preset(name: str) -> bool:
    target = preset_path(name)
    if not target.is_file():
        return False
    target.unlink()
    return True


# --- Поточна сцена ---------------------------------------------------------
def save(payload: dict) -> dict:
    """Записує стан і повертає його з полем `saved_at` (UTC, ISO-8601)."""
    data = {**payload, "saved_at": _now()}
    _atomic_write(state_path(), data)
    return data


def load() -> dict | None:
    """Повертає збережений стан або `None`, якщо файлу ще немає чи він битий."""
    target = state_path()
    if not target.is_file():
        return None
    try:
        with target.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear() -> bool:
    """Видаляє збережений стан. True, якщо файл існував."""
    target = state_path()
    if not target.is_file():
        return False
    target.unlink()
    return True
