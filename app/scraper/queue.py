"""
Work queues between the roles:

  WORK_STREAM  scheduler -> fetchers     {"search_id", "enqueued_at"}
  JOBS_STREAM  fetchers  -> dispatchers  {"search_id", "job_id", "seen_at"}

Delivery is at-least-once: a message stays pending until acked, and a message whose consumer died is
handed to another consumer after `reclaim_idle_ms`. Handlers must therefore be safe to run twice
(they are: hits and deliveries are insert-if-absent).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, List, Protocol, Set, Tuple

from app.queue import redis_streams

WORK_STREAM = "scraper:work"
JOBS_STREAM = "scraper:jobs"
FETCHERS = "fetchers"
DISPATCHERS = "dispatchers"

Message = Tuple[str, Dict[str, str]]


class Queue(Protocol):
    def put(self, stream: str, fields: Dict[str, str]) -> str: ...

    def get(self, stream: str, group: str, consumer: str, *, count: int = 10, block_ms: int = 5000) -> List[Message]: ...

    def ack(self, stream: str, group: str, msg_id: str) -> None: ...

    def backlog(self, stream: str, group: str) -> int:
        """Messages not yet acknowledged by the group (waiting + in progress)."""


class MemoryQueue:
    """Single-process queue for tests and `backend: memory`."""

    def __init__(self) -> None:
        self._items: Dict[str, Deque[Message]] = {}
        self._pending: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._cond = threading.Condition()
        self._n = 0

    def put(self, stream: str, fields: Dict[str, str]) -> str:
        with self._cond:
            self._n += 1
            msg_id = f"{self._n}-0"
            self._items.setdefault(stream, deque()).append((msg_id, dict(fields)))
            self._cond.notify_all()
            return msg_id

    def get(self, stream: str, group: str, consumer: str, *, count: int = 10, block_ms: int = 5000) -> List[Message]:
        deadline = time.monotonic() + block_ms / 1000
        with self._cond:
            while not self._items.get(stream):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)
            out: List[Message] = []
            items = self._items[stream]
            while items and len(out) < count:
                msg = items.popleft()
                self._pending.setdefault(stream, {})[msg[0]] = msg[1]
                out.append(msg)
            return out

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        with self._cond:
            self._pending.get(stream, {}).pop(msg_id, None)

    def backlog(self, stream: str, group: str) -> int:
        with self._cond:
            return len(self._items.get(stream, ())) + len(self._pending.get(stream, {}))


class RedisQueue:
    """Redis Streams with consumer groups (the primitives already in app/queue/redis_streams.py)."""

    def __init__(self, client, *, reclaim_idle_ms: int = 120_000) -> None:
        self._r = client
        self._reclaim_idle_ms = reclaim_idle_ms
        self._groups: Set[Tuple[str, str]] = set()

    def _ensure(self, stream: str, group: str) -> None:
        if (stream, group) not in self._groups:
            redis_streams.ensure_consumer_group(self._r, stream=stream, group=group)
            self._groups.add((stream, group))

    def put(self, stream: str, fields: Dict[str, str]) -> str:
        return redis_streams.xadd_work(self._r, stream=stream, fields=fields)

    def get(self, stream: str, group: str, consumer: str, *, count: int = 10, block_ms: int = 5000) -> List[Message]:
        self._ensure(stream, group)
        _, stale = redis_streams.xautoclaim(self._r, stream=stream, group=group, consumer=consumer,
                                            min_idle_ms=self._reclaim_idle_ms, count=count)
        stale = [(mid, fields) for mid, fields in (stale or []) if isinstance(fields, dict)]
        if stale:
            return stale
        out: List[Message] = []
        for _stream, entries in redis_streams.xreadgroup(self._r, stream=stream, group=group, consumer=consumer,
                                                         count=count, block_ms=block_ms) or []:
            out.extend((mid, fields) for mid, fields in entries)
        return out

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        redis_streams.xack(self._r, stream=stream, group=group, msg_id=msg_id)

    def backlog(self, stream: str, group: str) -> int:
        self._ensure(stream, group)
        for info in self._r.xinfo_groups(stream):
            if info.get("name") == group:
                return int(info.get("lag") or 0) + int(info.get("pending") or 0)
        return 0
