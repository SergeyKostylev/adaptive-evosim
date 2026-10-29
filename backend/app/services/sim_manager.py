"""Єдиний stateful-сервіс: стани RUNNING/PAUSED/STOPPED, ГА у фоновому
потоці, публікація подій у asyncio-черги для WebSocket (§1.2).

Прогін, що пройшов усі заплановані покоління без помилки, копіюється в
архів (`experiment_store.RunStore.archive`, §11); зупинений вручну чи упалий —
лишається тільки в `runs/`.

Потік ГА майже весь час чекає на пул процесів оцінки, тож GIL він у event
loop не забирає: живе прев'ю (`live_stream`) крутиться паралельно. Пауза —
між поколіннями; зупинка перериває поточне покоління (його результат не
пишеться), щоб «Стоп» не чекав повної оцінки популяції.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time

import numpy as np

from .. import config as C
from ..evolution.evaluator import EvaluationCancelled, Evaluator
from ..evolution.ga import GeneticAlgorithm, run_loop
from ..schemas import ExperimentConfig
from .experiment_store import RunStore

log = logging.getLogger(__name__)


class SimulationConflict(Exception):
    """Дія недоступна в поточному стані (напр. другий старт) → 409."""


class MetricsHub:
    """Розсилка подій `/ws/metrics`. Події дрібні, тож черга глибока; повільний
    клієнт втрачає найстаріші, а історію все одно дочитує з REST."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=C.METRICS_QUEUE_DEPTH)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def broadcast(self, packet: dict) -> None:
        """Лише з потоку event loop; з потоку ГА — через `call_soon_threadsafe`."""
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(packet)


class SimulationManager:
    """Один прогін ГА на застосунок."""

    def __init__(self) -> None:
        self.hub = MetricsHub()
        self.state = "STOPPED"
        self.run_id: str | None = None
        self.generation = 0
        self.generations_total = 0
        self.population = 0
        self.error: str | None = None
        #: Куди скопійовано завершений прогін (§11); `None` — не завершений
        self.archived: str | None = None
        #: Знімок конфігу поточного прогону — звідси `live_training` бере істоту й сцену
        self.cfg: ExperimentConfig | None = None
        #: `(покоління, геном)` найкращої особини останнього оціненого покоління (§9.4)
        self.latest_best: tuple[int, np.ndarray] | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()     # знятий = пауза
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started_at = 0.0
        self._paused_at: float | None = None
        self._paused_total = 0.0

    # --- стан ------------------------------------------------------------------
    @property
    def elapsed_s(self) -> float:
        """Час навчання без пауз."""
        if not self._started_at:
            return 0.0
        end = self._paused_at or time.monotonic()
        return round(end - self._started_at - self._paused_total, 2)

    def state_packet(self) -> dict:
        return {"type": "state", "state": self.state, "run_id": self.run_id,
                "generation": self.generation,
                "generations_total": self.generations_total, "error": self.error,
                "archived": self.archived}

    def _publish(self, packet: dict) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):     # loop уже зупиняється
                loop.call_soon_threadsafe(self.hub.broadcast, packet)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._publish(self.state_packet())

    # --- керування ---------------------------------------------------------------
    def start(self, cfg: ExperimentConfig, workers: int | None = None) -> str:
        with self._lock:
            if self.state != "STOPPED" or (self._thread and self._thread.is_alive()):
                raise SimulationConflict(f"прогін {self.run_id} уже триває ({self.state})")
            self._loop = asyncio.get_running_loop()
            store = RunStore.create(cfg)
            self.run_id = store.run_id
            self.generation = 0
            self.generations_total = cfg.ga.generations
            self.population = cfg.ga.population
            self.error = None
            self.archived = None
            self.cfg = cfg
            self.latest_best = None
            self._stop.clear()
            self._running.set()
            self._started_at = time.monotonic()
            self._paused_at = None
            self._paused_total = 0.0
            self._thread = threading.Thread(
                target=self._run, args=(cfg, store, workers),
                name=f"ga-{store.run_id}", daemon=True)
            self._set_state("RUNNING")
            self._thread.start()
            return store.run_id

    def pause(self) -> None:
        with self._lock:
            if self.state != "RUNNING":
                raise SimulationConflict(f"призупинити можна лише RUNNING, зараз {self.state}")
            self._running.clear()
            self._paused_at = time.monotonic()
            self._set_state("PAUSED")

    def resume(self) -> None:
        with self._lock:
            if self.state != "PAUSED":
                raise SimulationConflict(f"продовжити можна лише PAUSED, зараз {self.state}")
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
            self._set_state("RUNNING")
            self._running.set()

    def stop(self, timeout: float | None = None) -> None:
        """Блокує до завершення потоку — викликати через `asyncio.to_thread`."""
        thread = self._thread
        self._stop.set()
        self._running.set()          # розбудити паузу, щоб потік побачив стоп
        if thread is not None:
            thread.join(timeout)

    # --- потік ГА ----------------------------------------------------------------
    def _before_generation(self) -> bool:
        while not self._running.wait(timeout=0.2):
            if self._stop.is_set():
                return False
        return not self._stop.is_set()

    def _on_generation(self, stats: dict, ga: GeneticAlgorithm) -> None:
        self.generation = stats["generation"]
        # Кортеж замінюється цілком — читач з event loop бачить або старий, або новий
        self.latest_best = (stats["generation"], ga.generation_best)
        self._publish({"type": "generation", "run_id": self.run_id, **stats})

    def _run(self, cfg: ExperimentConfig, store: RunStore, workers: int | None) -> None:
        try:
            ga = GeneticAlgorithm(cfg)
            with Evaluator(cfg, workers) as evaluator:
                run_loop(ga, evaluator, store, until=cfg.ga.generations,
                         before_generation=self._before_generation,
                         on_generation=lambda stats: self._on_generation(stats, ga),
                         cancel=self._stop)
            # В архів — лише прогін, що пройшов усі заплановані покоління (§11)
            if ga.generation > cfg.ga.generations and not self._stop.is_set():
                self.archived = str(store.archive().relative_to(C.DATA_DIR))
        except EvaluationCancelled:
            pass
        except Exception as exc:          # noqa: BLE001 — потік не має падати мовчки
            log.exception("прогін %s упав", store.run_id)
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = time.monotonic()      # заморозити elapsed_s
            self._set_state("STOPPED")


#: Єдиний екземпляр на застосунок
manager = SimulationManager()
