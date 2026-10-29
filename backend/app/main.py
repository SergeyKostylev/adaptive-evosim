"""FastAPI-застосунок: REST + WebSocket-канали (§9.4–9.6).

Етапи 1–2: валідація сутностей, бібліотеки конструктора, живий перегляд
(`POST /api/preview` + `/ws/live`). Етап 3: керування ГА (`/api/simulation/*`),
метрики поколінь (`/api/metrics/generations`, `/ws/metrics`), найкращий геном
і прев'ю геному (`source: "genome" | "best"`). Етап 4: `source: "live_training"`
— перемикач Headless ↔ Live.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config as C
from .core.world import World
from .schemas import (
    CreatureConfig,
    CreatureList,
    CreatureMeta,
    CreatureValidationResponse,
    EditorState,
    EditorStateResponse,
    EnvironmentConfig,
    ExperimentConfig,
    GenerationRows,
    GenerationStats,
    GenomeJSON,
    PredatorLibrary,
    PredatorMeta,
    PredatorParams,
    PredatorValidationResponse,
    PresetList,
    PresetMeta,
    PreviewRequest,
    PreviewResponse,
    PreviewState,
    SavedCreature,
    SavedPredator,
    SimulationControlResponse,
    SimulationStatus,
)
from .evolution import genome as genome_ops
from .services import editor_store, live_stream
from .services.experiment_store import RunNotFound, RunStore
from .services.sim_manager import SimulationConflict
from .services.sim_manager import manager as sim

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


# --- Керування ГА (§9.4) ------------------------------------------------------
def _conflict(exc: Exception) -> StarletteHTTPException:
    return StarletteHTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _open_run(run_id: str | None) -> RunStore:
    """Прогін за `run_id`; без нього — поточний (або останній) прогін менеджера."""
    run_id = run_id or sim.run_id
    if not run_id:
        raise StarletteHTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                     detail="Жодного прогону ще не запускали")
    try:
        return RunStore.open(run_id)
    except RunNotFound as exc:
        raise StarletteHTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@app.post("/api/simulation/start", response_model=SimulationControlResponse)
async def simulation_start(payload: ExperimentConfig) -> SimulationControlResponse:
    """Стартує прогін у фоновому потоці; `409`, якщо вже запущено."""
    try:
        run_id = sim.start(payload)
    except SimulationConflict as exc:
        raise _conflict(exc)
    return SimulationControlResponse(state=sim.state, run_id=run_id)


@app.post("/api/simulation/pause", response_model=SimulationControlResponse)
async def simulation_pause() -> SimulationControlResponse:
    """Пауза між поколіннями: поточне покоління дооцінюється й записується."""
    try:
        sim.pause()
    except SimulationConflict as exc:
        raise _conflict(exc)
    return SimulationControlResponse(state=sim.state, run_id=sim.run_id)


@app.post("/api/simulation/resume", response_model=SimulationControlResponse)
async def simulation_resume() -> SimulationControlResponse:
    try:
        sim.resume()
    except SimulationConflict as exc:
        raise _conflict(exc)
    return SimulationControlResponse(state=sim.state, run_id=sim.run_id)


@app.post("/api/simulation/stop", response_model=SimulationControlResponse)
async def simulation_stop() -> SimulationControlResponse:
    """Зупиняє прогін (поточне покоління відкидається) і чекає завершення потоку."""
    await asyncio.to_thread(sim.stop)
    return SimulationControlResponse(state=sim.state, run_id=sim.run_id)


@app.get("/api/simulation/status", response_model=SimulationStatus)
async def simulation_status() -> SimulationStatus:
    return SimulationStatus(
        state=sim.state,
        run_id=sim.run_id,
        generation=sim.generation,
        generations_total=sim.generations_total,
        population=sim.population,
        elapsed_s=sim.elapsed_s,
        mode="live" if live_stream.manager.active else "headless",
    )


@app.get("/api/metrics/generations", response_model=GenerationRows)
async def metrics_generations(run_id: str | None = None, from_gen: int = 0) -> GenerationRows:
    store = _open_run(run_id)
    return GenerationRows(
        run_id=store.run_id,
        rows=[GenerationStats.model_validate(r) for r in store.read_generations(from_gen)],
    )


@app.get("/api/genome/best", response_model=GenomeJSON)
async def genome_best(run_id: str | None = None) -> GenomeJSON:
    store = _open_run(run_id)
    data = store.load_best_genome()
    if data is None:
        raise StarletteHTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                     detail=f"У прогоні {store.run_id} ще немає оціненого покоління")
    return GenomeJSON.model_validate(data)


@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket) -> None:
    """Події прогону (§9.6): пакет `state` одразу й на кожну зміну, `generation` —
    після кожного покоління. Історію клієнт бере з `GET /api/metrics/generations`."""
    await websocket.accept()
    queue = sim.hub.subscribe()
    try:
        await websocket.send_json(sim.state_packet())
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        sim.hub.unsubscribe(queue)


# --- Живий перегляд (§9.4, §9.5) -------------------------------------------
@app.post("/api/preview", response_model=PreviewResponse)
async def start_preview(payload: PreviewRequest) -> PreviewResponse:
    """Стартує live-епізод; кадри йдуть у `/ws/live`.

    `demo` — тестова синусоїда (§2.4); `genome` — геном із тіла запиту;
    `best` — найкращий геном прогону: істота й сцена беруться зі знімка
    прогону, а сам геном перечитується на кожне нове коло епізоду, тож
    прев'ю під час навчання саме підхоплює свіжий рекорд. `live_training` —
    найкраща особина останнього оціненого покоління поточного прогону, теж
    на кожне коло. Для цих двох кола різняться сідом і точкою появи (§9.4).
    """
    def generation_of():                    # покоління для HUD (§9.5)
        return None

    varied = payload.source in ("best", "live_training")

    if payload.source == "live_training":
        if payload.run_id and payload.run_id != sim.run_id:
            raise StarletteHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"live_training показує лише поточний прогін ({sim.run_id})")
        if sim.cfg is None or sim.latest_best is None:
            raise StarletteHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Поточний прогін ще не оцінив жодного покоління")
        run_cfg = sim.cfg
        creature, environment, weights = (
            run_cfg.creature, run_cfg.environment, run_cfg.fitness_weights)
        latest = sim.latest_best

        def refresh():
            nonlocal latest
            # Прогін міг змінитись: новий старт — новий конфіг, тож геном
            # чужого прогону сюди не потрапляє
            if sim.cfg is run_cfg and sim.latest_best is not None:
                latest = sim.latest_best
            return latest

        def current_genome():
            return refresh()[1]

        def generation_of():
            return latest[0]

    elif payload.source == "best":
        store = _open_run(payload.run_id)
        run_cfg = store.load_config()
        creature, environment, weights = (
            run_cfg.creature, run_cfg.environment, run_cfg.fitness_weights)
        if store.load_best_genome() is None:
            raise StarletteHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"У прогоні {store.run_id} ще немає оціненого покоління")

        def current_genome():
            return genome_ops.from_json(store.load_best_genome(), creature)

        try:
            current_genome()
        except genome_ops.GenomeMismatch as exc:
            # Прогін іншого етапу: набір сенсорів (`n_in`) відтоді змінився
            raise StarletteHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    else:
        creature = payload.creature
        environment = payload.environment or EnvironmentConfig()
        weights = None
        fixed = None
        if payload.source == "genome":
            try:
                fixed = genome_ops.from_json(payload.genome, creature)
            except genome_ops.GenomeMismatch as exc:
                raise StarletteHTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

        def current_genome():
            return fixed

    # Геном із прогону показується в тому горизонті, у якому його оцінювали,
    # якщо запит не просить іншого (§9.4)
    episode_seconds = payload.episode_seconds
    if varied and "episode_seconds" not in payload.model_fields_set:
        episode_seconds = run_cfg.ga.episode_seconds

    def make_world(k: int) -> World:
        shift = k if varied else 0
        world = World(
            creature, environment, genome=current_genome(), seed=payload.seed + shift,
            episode_seconds=episode_seconds,
            spawn_index=payload.spawn_index + shift, weights=weights,
        )
        world.generation = generation_of()
        return world

    session = live_stream.PreviewSession(
        live_stream.manager.hub, make_world,
        source=payload.source, speed=payload.speed, loop=payload.loop,
    )
    await live_stream.manager.start(session)
    return PreviewResponse(
        preview_id=session.preview_id,
        source=payload.source,
        n_joints=creature.n_joints,
        n_in=creature.n_in,
        genome_size=creature.genome_size,
        episode_seconds=episode_seconds,
    )


@app.delete("/api/preview", response_model=PreviewState)
async def stop_preview() -> PreviewState:
    """Зупиняє прев'ю; headless-навчання (етап 3) це не чіпає."""
    await live_stream.manager.stop()
    return PreviewState(active=False)


@app.get("/api/preview", response_model=PreviewState)
async def preview_state() -> PreviewState:
    session = live_stream.manager.current
    return PreviewState(
        active=session is not None,
        preview_id=session.preview_id if session else None,
        source=session.source if session else None,
    )


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Канал кадрів (§9.5).

    Нового клієнта одразу наздоганяє збережений пакет `scene`: статика сцени
    в кадрах не повторюється, тож без цього не було б що малювати.
    """
    await websocket.accept()
    hub = live_stream.manager.hub
    queue = hub.subscribe()
    try:
        if hub.scene is not None:
            await websocket.send_json(hub.scene)
        while True:
            packet = await queue.get()
            await websocket.send_json(packet)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)


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
