"""
Request budget per outbound IP, shared by every worker using that IP.

Upwork's limits are per IP (research/upwork_recon/04): the search page returned 429 after roughly
150-190 page requests in 8-10 minutes. So the budget belongs to the egress, not to a process.

  acquire(bucket, limit, window_s) -> 0.0 if the call may go now, else seconds to wait
  pause(bucket, seconds)           -> everyone on this egress backs off (used after a 429)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Protocol

API = "api"
PAGE = "page"


class RateLimiter(Protocol):
    def acquire(self, bucket: str, limit: int, window_s: float) -> float: ...

    def pause(self, bucket: str, seconds: float) -> None: ...


class MemoryRateLimiter:
    """Sliding window, in-process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._calls: Dict[str, Deque[float]] = {}
        self._paused_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, bucket: str, limit: int, window_s: float) -> float:
        with self._lock:
            now = self._clock()
            paused = self._paused_until.get(bucket, 0.0) - now
            if paused > 0:
                return paused
            calls = self._calls.setdefault(bucket, deque())
            while calls and calls[0] <= now - window_s:
                calls.popleft()
            if len(calls) >= limit:
                return calls[0] + window_s - now
            calls.append(now)
            return 0.0

    def pause(self, bucket: str, seconds: float) -> None:
        with self._lock:
            self._paused_until[bucket] = max(self._paused_until.get(bucket, 0.0), self._clock() + seconds)


class RedisRateLimiter:
    """Fixed-window counter in Redis: one INCR per call, shared across processes and machines."""

    def __init__(self, client, *, egress_id: str = "default", prefix: str = "scraper",
                 clock: Callable[[], float] = time.time) -> None:
        self._r = client
        self._base = f"{prefix}:rl:{egress_id}"
        self._clock = clock

    def acquire(self, bucket: str, limit: int, window_s: float) -> float:
        paused_ms = self._r.pttl(f"{self._base}:{bucket}:pause")
        if paused_ms and paused_ms > 0:
            return paused_ms / 1000
        now = self._clock()
        window = int(now // window_s)
        key = f"{self._base}:{bucket}:{window}"
        count = self._r.incr(key)
        if count == 1:
            self._r.expire(key, int(window_s * 2) + 1)
        if count > limit:
            return (window + 1) * window_s - now
        return 0.0

    def pause(self, bucket: str, seconds: float) -> None:
        self._r.set(f"{self._base}:{bucket}:pause", "1", px=max(1, int(seconds * 1000)))


def wait_for(limiter: RateLimiter, bucket: str, limit: int, window_s: float, *,
             sleep: Callable[[float], None] = time.sleep) -> float:
    """Block until the call is allowed. Returns the total time waited."""
    waited = 0.0
    while True:
        delay = limiter.acquire(bucket, limit, window_s)
        if delay <= 0:
            return waited
        sleep(delay + 0.01)
        waited += delay
