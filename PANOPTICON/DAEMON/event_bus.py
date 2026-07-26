"""Bus d'événements inter-modules (Redis ou mémoire)."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)


class InMemoryEventBus:
    """Queue pub/sub en mémoire pour installation mono-machine."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(channel, []))
        for queue in queues:
            await queue.put({"channel": channel, "payload": payload})

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers[channel].append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                if queue in self._subscribers[channel]:
                    self._subscribers[channel].remove(queue)


class RedisEventBus:
    """Pub/sub Redis pour déploiements multi-processus."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url)

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        await self._redis.publish(channel, json.dumps(payload))

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        if not self._redis:
            await self.connect()
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield {"channel": channel, "payload": json.loads(data)}
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


def create_event_bus(redis_url: str = "") -> InMemoryEventBus | RedisEventBus:
    if redis_url.strip():
        return RedisEventBus(redis_url)
    return InMemoryEventBus()


async def fan_out(
    bus: InMemoryEventBus | RedisEventBus,
    channel: str,
    handler: Callable[[dict[str, Any]], Any],
) -> None:
    async for message in bus.subscribe(channel):
        try:
            result = handler(message["payload"])
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Erreur handler bus sur canal %s", channel)
