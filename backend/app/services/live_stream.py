"""Серіалізація кадрів у FramePacket і троттлінг до 60 fps (§9.5).

Три частини:

* `LiveHub` — розсилка пакетів підписникам `/ws/live`. Черга кожного клієнта
  має глибину 1: відстаючий бачить **найновіший** кадр, а не хвіст застарілих.
* `PreviewSession` — епізод у реальному часі як asyncio-задача. Фізика тут
  коштує десятки мікросекунд на кадр, тож окремий потік не потрібен і лише
  зіпсував би детермінізм.
* `PreviewManager` — рівно одне активне прев'ю на застосунок.

Headless-шлях (`evaluator.py`) цим модулем не користується: там немає ні
кадрів, ні пауз (§1.2).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Callable

from .. import config as C
from ..core.world import World


class LiveHub:
    """Розсилка `scene`/`frame` активним WebSocket-клієнтам (§9.5)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        #: Останній пакет `scene` — клієнт, що підключився посеред епізоду,
        #: має отримати статику сцени одразу, а не чекати нового прев'ю
        self._scene: dict | None = None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=C.LIVE_QUEUE_DEPTH)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def scene(self) -> dict | None:
        return self._scene

    @property
    def listeners(self) -> int:
        return len(self._subscribers)

    def publish_scene(self, packet: dict) -> None:
        self._scene = packet
        self._broadcast(packet)

    def publish_frame(self, packet: dict) -> None:
        self._broadcast(packet)

    def clear_scene(self) -> None:
        self._scene = None

    def _broadcast(self, packet: dict) -> None:
        """Drop-oldest: місце в черзі звільняється під найновіший пакет."""
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(packet)


class PreviewSession:
    """Один live-епізод: крутить `World` у темпі `LIVE_FPS` і шле кадри."""

    def __init__(
        self,
        hub: LiveHub,
        make_world: Callable[[int], World],
        *,
        source: str,
        speed: float = 1.0,
        loop: bool = True,
        fps: int = C.LIVE_FPS,
    ) -> None:
        self.hub = hub
        self.make_world = make_world
        self.source = source
        self.speed = speed
        self.loop = loop
        self.fps = fps
        self.preview_id = f"pv-{secrets.token_hex(3)}"
        #: `make_world(k)` — світ для кола `k`; джерело саме вирішує, чим кола різняться
        self.world: World = make_world(0)
        self.episodes = 0
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    @property
    def steps_per_frame(self) -> int:
        """Скільки кроків `DT` припадає на один кадр при заданому темпі."""
        return max(1, round(self.speed / (self.fps * C.DT)))

    def scene_packet(self) -> dict:
        return {**self.world.scene_packet(),
                "preview_id": self.preview_id, "source": self.source}

    def frame_packet(self) -> dict:
        return {**self.world.snapshot(),
                "preview_id": self.preview_id,
                "sim_speed": self.speed,
                "episode": self.episodes}

    def start(self) -> None:
        self.hub.publish_scene(self.scene_packet())
        self._task = asyncio.create_task(self._run(), name=f"preview-{self.preview_id}")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.hub.clear_scene()

    async def _run(self) -> None:
        """Цикл кадрів у реальному часі.

        Темп тримається за абсолютним дедлайном, а не `sleep(frame_dt)`: інакше
        час обчислень накопичувався б і прев'ю поволі відставало від годинника.
        """
        frame_dt = 1.0 / self.fps
        next_frame = time.perf_counter()
        try:
            while not self._stopped.is_set():
                for _ in range(self.steps_per_frame):
                    if not self.world.alive:
                        break
                    self.world.step()

                self.hub.publish_frame(self.frame_packet())

                if not self.world.alive:
                    if not self.loop:
                        break
                    self.episodes += 1
                    self.world = self.make_world(self.episodes)
                    self.hub.publish_scene(self.scene_packet())

                next_frame += frame_dt
                delay = next_frame - time.perf_counter()
                if delay < 0:
                    next_frame = time.perf_counter()    # відстали — не наздоганяємо ривком
                    delay = 0
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise


class PreviewManager:
    """Рівно одне активне прев'ю: канал `/ws/live` один на застосунок."""

    def __init__(self) -> None:
        self.hub = LiveHub()
        self.current: PreviewSession | None = None

    @property
    def active(self) -> bool:
        return self.current is not None

    async def start(self, session: PreviewSession) -> PreviewSession:
        await self.stop()
        self.current = session
        session.start()
        return session

    async def stop(self) -> bool:
        if self.current is None:
            return False
        await self.current.stop()
        self.current = None
        return True


#: Єдиний екземпляр на застосунок (інстанціюється разом із FastAPI)
manager = PreviewManager()
