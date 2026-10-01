"""
Liveness: every role says "I'm alive" every few seconds; the entry expires by itself if the role stops.
The watchdog and the dashboard read this to know which roles are running, on any machine.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass(frozen=True, slots=True)
class RoleBeat:
    role: str
    name: str  # which process: "<role>-<host>-<pid>"
    at: float  # epoch seconds of the last beat
    info: Dict[str, Any] = field(default_factory=dict)


class Presence(Protocol):
    def beat(self, role: str, name: str, info: Optional[Dict[str, Any]] = None, *, ttl_s: float = 90.0) -> None: ...

    def alive(self) -> List[RoleBeat]: ...


class MemoryPresence:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._beats: Dict[str, tuple] = {}

    def beat(self, role: str, name: str, info: Optional[Dict[str, Any]] = None, *, ttl_s: float = 90.0) -> None:
        now = self._clock()
        self._beats[f"{role}:{name}"] = (RoleBeat(role, name, now, dict(info or {})), now + ttl_s)

    def alive(self) -> List[RoleBeat]:
        now = self._clock()
        return sorted((b for b, expires in self._beats.values() if expires > now), key=lambda b: (b.role, b.name))


class RedisPresence:
    def __init__(self, client, *, prefix: str = "scraper", clock: Callable[[], float] = time.time) -> None:
        self._r = client
        self._prefix = f"{prefix}:hb:"
        self._clock = clock

    def beat(self, role: str, name: str, info: Optional[Dict[str, Any]] = None, *, ttl_s: float = 90.0) -> None:
        payload = json.dumps({"role": role, "name": name, "at": self._clock(), "info": info or {}}, default=str)
        self._r.set(f"{self._prefix}{role}:{name}", payload, ex=max(1, int(ttl_s)))

    def alive(self) -> List[RoleBeat]:
        beats = []
        for key in self._r.scan_iter(f"{self._prefix}*"):
            raw = self._r.get(key)
            if raw:
                d = json.loads(raw)
                beats.append(RoleBeat(d["role"], d["name"], float(d["at"]), d.get("info") or {}))
        return sorted(beats, key=lambda b: (b.role, b.name))


class Heartbeat:
    """Callable a role invokes every loop; actually writes at most once per `every_s`, and never raises
    (liveness reporting must not be able to take a worker down)."""

    def __init__(self, presence: Presence, role: str, name: str, *, every_s: float = 20.0,
                 info: Optional[Callable[[], Dict[str, Any]]] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._presence, self._role, self._name = presence, role, name
        self._every_s, self._info, self._clock = every_s, info, clock
        self._next = 0.0

    def __call__(self) -> None:
        now = self._clock()
        if now < self._next:
            return
        self._next = now + self._every_s
        try:
            self._presence.beat(self._role, self._name, self._info() if self._info else None)
        except Exception:  # noqa: BLE001
            pass
