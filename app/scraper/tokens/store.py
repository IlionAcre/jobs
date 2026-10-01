"""
Where the current token lives. One token per egress (outbound IP), shared by every worker on it.
The lock makes minting single-flight: with ten fetchers, one mints and the other nine wait.
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from typing import Dict, Iterator, Optional, Protocol

from app.scraper.models import Token


class TokenStore(Protocol):
    def get(self, egress_id: str) -> Optional[Token]: ...

    def put(self, token: Token) -> None: ...

    def delete(self, egress_id: str, value: str) -> None:
        """Remove the token only if it still is `value` (another worker may have replaced it)."""

    def lock(self, egress_id: str, ttl_s: float):
        """Context manager yielding True if this caller holds the mint lock."""


class MemoryTokenStore:
    def __init__(self) -> None:
        self._tokens: Dict[str, Token] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, egress_id: str) -> Optional[Token]:
        return self._tokens.get(egress_id)

    def put(self, token: Token) -> None:
        self._tokens[token.egress_id] = token

    def delete(self, egress_id: str, value: str) -> None:
        with self._guard:
            current = self._tokens.get(egress_id)
            if current is not None and current.value == value:
                del self._tokens[egress_id]

    @contextmanager
    def lock(self, egress_id: str, ttl_s: float) -> Iterator[bool]:
        with self._guard:
            lock = self._locks.setdefault(egress_id, threading.Lock())
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()


_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_DELETE_IF = (
    "local v = redis.call('get', KEYS[1]) "
    "if v and cjson.decode(v)['value'] == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
)


class RedisTokenStore:
    def __init__(self, client, *, prefix: str = "scraper") -> None:
        self._r = client
        self._prefix = prefix

    def _key(self, egress_id: str) -> str:
        return f"{self._prefix}:token:{egress_id}"

    def get(self, egress_id: str) -> Optional[Token]:
        raw = self._r.get(self._key(egress_id))
        return Token(**json.loads(raw)) if raw else None

    def put(self, token: Token) -> None:
        self._r.set(self._key(token.egress_id), json.dumps(asdict(token)))

    def delete(self, egress_id: str, value: str) -> None:
        self._r.eval(_DELETE_IF, 1, self._key(egress_id), value)

    @contextmanager
    def lock(self, egress_id: str, ttl_s: float) -> Iterator[bool]:
        key, owner = f"{self._prefix}:mintlock:{egress_id}", uuid.uuid4().hex
        acquired = bool(self._r.set(key, owner, nx=True, ex=max(1, int(ttl_s))))
        try:
            yield acquired
        finally:
            if acquired:
                self._r.eval(_RELEASE, 1, key, owner)
