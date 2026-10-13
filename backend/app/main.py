"""FastAPI-застосунок: REST + (пізніше) WebSocket-канали.

Етап 1 (PLAN.md): лише `POST /api/creature/validate` та заглушка
`GET /api/simulation/status`. Решта ендпоінтів §9.4 додається на етапах 2–5.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config as C
from .schemas import (
    CreatureConfig,
    CreatureList,
    CreatureMeta,
    CreatureValidationResponse,
    EditorState,
    EditorStateResponse,
    PredatorLibrary,
    PredatorMeta,
    PredatorParams,
    PredatorValidationResponse,
    PresetList,
    PresetMeta,
    SavedCreature,
    SavedPredator,
    SimulationStatus,
)
from .services import editor_store

app = FastAPI(
    title="Adaptive EvoSim API",
    version="0.1.0",
    description="Еволюція плаваючих істот у 2D в'язкому середовищі",
)


# ---------------------------------------------------------------------------
# Єдиний формат помилок (§9.4)
# ---------------------------------------------------------------------------
def error_response(code: str, message: str, details: list | None = None, http_status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": code, "message": message, "details": details or []}},
    )


def format_validation_errors(
    exc: ValidationError | RequestValidationError,
    strip_body_prefix: bool = False,
) -> list[str]:
    """Помилки Pydantic → читабельні рядки «шлях: повідомлення».

    `strip_body_prefix` знімає службовий елемент `body`, яким FastAPI позначає
    тіло запиту. Поза цим випадком `body` — справжнє поле CreatureConfig.
    """
    out: list[str] = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ())]
        if strip_body_prefix and loc and loc[0] == "body":
            loc = loc[1:]
        where = ".".join(loc) if loc else "(корінь)"
        msg = err.get("msg", "невалідне значення")
        msg = msg.removeprefix("Value error, ")
        out.append(f"{where}: {msg}")
    return out


@app.exception_handler(RequestValidationError)
async def _on_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        "VALIDATION_ERROR",
        "Тіло запиту не відповідає схемі",
        format_validation_errors(exc, strip_body_prefix=True),
        status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(StarletteHTTPException)
async def _on_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {
        400: "INVALID_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 422: "VALIDATION_ERROR",
    }
    return error_response(
        codes.get(exc.status_code, "HTTP_ERROR"),
        str(exc.detail),
        http_status=exc.status_code,
    )


# ---------------------------------------------------------------------------
# REST (§9.4)
# ---------------------------------------------------------------------------
@app.post("/api/creature/validate", response_model=CreatureValidationResponse)
async def validate_creature(payload: dict) -> CreatureValidationResponse:
    """Валідація морфології + похідні величини.

    Приймає «сире» тіло запиту, щоб невалідна конфігурація повертала
    `{valid:false, errors:[...]}` зі статусом 200 (контракт §9.4), а не 422.
    """
    try:
        creature = CreatureConfig.model_validate(payload)
    except ValidationError as exc:
        return CreatureValidationResponse(valid=False, errors=format_validation_errors(exc))
    return CreatureValidationResponse(valid=True, **creature.derived())


@app.post("/api/predator/validate", response_model=PredatorValidationResponse)
async def validate_predator(payload: dict) -> PredatorValidationResponse:
    """Перевірка параметрів хижака (§8.1) — симетрично до істоти.

    Правила діапазонів живуть у схемі, тож фронтенд їх не дублює.
    """
    try:
        predator = PredatorParams.model_validate(payload)
    except ValidationError as exc:
        return PredatorValidationResponse(valid=False, errors=format_validation_errors(exc))
    return PredatorValidationResponse(
        valid=True,
        r_catch_effective=max(predator.r_catch, predator.r_body),
        aura_diameter=predator.r_aura * 2,
    )


@app.get("/api/simulation/status", response_model=SimulationStatus)
async def simulation_status() -> SimulationStatus:
    """Заглушка етапу 1: справжній стан з'явиться разом із SimulationManager (етап 3)."""
    return SimulationStatus(state="STOPPED")


# --- Стан конструктора: зберігається у проєкті (experiments/editor/) --------
@app.get("/api/editor/state", response_model=EditorStateResponse)
async def get_editor_state() -> EditorStateResponse:
    """Остання збережена сцена; `exists=false`, якщо користувач ще не зберігав."""
    data = editor_store.load()
    if data is None:
        return EditorStateResponse(exists=False)
    try:
        state = EditorState.model_validate(data)
    except ValidationError:
        # Файл редагували вручну й поламали структуру — не валимо UI
        return EditorStateResponse(exists=False)
    return EditorStateResponse(exists=True, saved_at=state.saved_at, state=state)


@app.put("/api/editor/state", response_model=EditorStateResponse)
async def put_editor_state(payload: EditorState) -> EditorStateResponse:
    _check_size(payload)
    saved = editor_store.save(payload.model_dump(mode="json", exclude={"saved_at"}))
    return EditorStateResponse(
        exists=True,
        saved_at=saved["saved_at"],
        state=EditorState.model_validate(saved),
    )


@app.delete("/api/editor/state", response_model=EditorStateResponse)
async def delete_editor_state() -> EditorStateResponse:
    editor_store.clear()
    return EditorStateResponse(exists=False)


# --- Іменовані варіанти сцени ----------------------------------------------
def _check_size(payload: EditorState) -> None:
    blob = payload.model_dump_json(exclude={"saved_at"})
    if len(blob.encode("utf-8")) > editor_store.MAX_STATE_BYTES:
        raise StarletteHTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Стан редактора перевищує {editor_store.MAX_STATE_BYTES} байт",
        )


@app.get("/api/editor/presets", response_model=PresetList)
async def list_presets() -> PresetList:
    return PresetList(
        presets=[PresetMeta.model_validate(m) for m in editor_store.list_presets()],
        limit=editor_store.MAX_PRESETS,
    )


@app.put("/api/editor/presets/{name}", response_model=PresetMeta)
async def put_preset(name: str, payload: EditorState) -> PresetMeta:
    _check_size(payload)
    try:
        meta = editor_store.save_preset(name, payload.model_dump(mode="json", exclude={"saved_at"}))
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:      # ліміт кількості варіантів
        raise StarletteHTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return PresetMeta.model_validate(meta)


@app.get("/api/editor/presets/{name}", response_model=EditorStateResponse)
async def get_preset(name: str) -> EditorStateResponse:
    try:
        data = editor_store.load_preset(name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if data is None:
        raise StarletteHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Варіант «{name}» не знайдено",
        )
    try:
        state = EditorState.model_validate(data)
    except ValidationError as exc:
        raise StarletteHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Файл варіанта має неочікувану структуру: {format_validation_errors(exc)}",
        )
    return EditorStateResponse(exists=True, saved_at=state.saved_at, state=state)


@app.delete("/api/editor/presets/{name}", response_model=PresetList)
async def delete_preset(name: str) -> PresetList:
    try:
        editor_store.delete_preset(name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await list_presets()


# --- Бібліотека істот -------------------------------------------------------
@app.get("/api/editor/creatures", response_model=CreatureList)
async def list_creatures() -> CreatureList:
    """Список збережених морфологій (разом із самою морфологією — для мініатюр)."""
    rows = []
    for meta in editor_store.list_items("creatures"):
        try:
            rows.append(CreatureMeta.model_validate(meta))
        except ValidationError:
            # Файл редагували вручну й поламали морфологію — показуємо без неї
            rows.append(CreatureMeta.model_validate({**meta, "creature": None}))
    return CreatureList(creatures=rows, limit=editor_store.MAX_PRESETS)


@app.put("/api/editor/creatures/{name}", response_model=CreatureMeta)
async def put_creature(name: str, payload: SavedCreature) -> CreatureMeta:
    """На відміну від сцени-чернетки, морфологія тут валідується за §9.1."""
    try:
        meta = editor_store.save_item(
            "creatures", name, payload.model_dump(mode="json", exclude={"saved_at"}))
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise StarletteHTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return CreatureMeta.model_validate(meta)


@app.get("/api/editor/creatures/{name}", response_model=SavedCreature)
async def get_creature(name: str) -> SavedCreature:
    try:
        data = editor_store.load_item("creatures", name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if data is None:
        raise StarletteHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Істоту «{name}» не знайдено")
    try:
        return SavedCreature.model_validate(data)
    except ValidationError as exc:
        raise StarletteHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Файл істоти має неочікувану структуру: {format_validation_errors(exc)}")


@app.delete("/api/editor/creatures/{name}", response_model=CreatureList)
async def delete_creature(name: str) -> CreatureList:
    try:
        editor_store.delete_item("creatures", name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await list_creatures()


# --- Бібліотека хижаків -----------------------------------------------------
@app.get("/api/editor/predators", response_model=PredatorLibrary)
async def list_predators() -> PredatorLibrary:
    rows = []
    for meta in editor_store.list_items("predators"):
        try:
            rows.append(PredatorMeta.model_validate(meta))
        except ValidationError:
            rows.append(PredatorMeta.model_validate({**meta, "predator": None}))
    return PredatorLibrary(predators=rows, limit=editor_store.MAX_PRESETS)


@app.put("/api/editor/predators/{name}", response_model=PredatorMeta)
async def put_predator(name: str, payload: SavedPredator) -> PredatorMeta:
    try:
        meta = editor_store.save_item(
            "predators", name, payload.model_dump(mode="json", exclude={"saved_at"}))
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise StarletteHTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return PredatorMeta.model_validate(meta)


@app.get("/api/editor/predators/{name}", response_model=SavedPredator)
async def get_predator(name: str) -> SavedPredator:
    try:
        data = editor_store.load_item("predators", name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if data is None:
        raise StarletteHTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Хижака «{name}» не знайдено")
    try:
        return SavedPredator.model_validate(data)
    except ValidationError as exc:
        raise StarletteHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Файл хижака має неочікувану структуру: {format_validation_errors(exc)}")


@app.delete("/api/editor/predators/{name}", response_model=PredatorLibrary)
async def delete_predator(name: str) -> PredatorLibrary:
    try:
        editor_store.delete_item("predators", name)
    except editor_store.InvalidPresetName as exc:
        raise StarletteHTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await list_predators()


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": app.version, "dt": C.DT}


# ---------------------------------------------------------------------------
# Локальна розробка без Docker: SERVE_STATIC=1 (§12)
# ---------------------------------------------------------------------------
if os.getenv("SERVE_STATIC") == "1":
    from fastapi.staticfiles import StaticFiles

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
