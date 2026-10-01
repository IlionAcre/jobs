from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from app.scraper.config import TokensConfig
from app.scraper.errors import MintChainExhausted, RateLimited
from app.scraper.models import Token
from app.scraper.tokens.chain import MinterChain
from app.scraper.tokens.store import TokenStore

log = logging.getLogger("scraper.tokens")

_MINT_LOCK_TTL_S = 300.0  # longer than the slowest chain (browser fallbacks with retries)
_WAIT_FOR_OTHER_MINTER_S = 2.0
_REFRESH_RETRY_AFTER_S = 600.0  # after a failed early refresh, leave the working token alone for a while


class TokenManager:
    """Hands out the current token and keeps it fresh.

    - no token / too old      -> mint now (callers block; only one worker mints)
    - older than refresh age  -> mint a replacement while the old one still works; if that fails,
                                 keep using the old one and try again later
    - invalidate(value)       -> the API rejected it; drop it so the next get() mints
    """

    def __init__(
        self,
        store: TokenStore,
        chain: MinterChain,
        cfg: TokensConfig,
        *,
        egress_id: str = "default",
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        before_mint: Optional[Callable[[], None]] = None,
    ) -> None:
        self._store = store
        self._chain = chain
        self._cfg = cfg
        self._egress_id = egress_id
        self._clock = clock
        self._sleep = sleep
        self._before_mint = before_mint  # e.g. the page rate limiter
        self._next_refresh_attempt = 0.0

    def get(self) -> str:
        token = self._store.get(self._egress_id)
        now = self._clock()
        if token is None or token.age_s(now) > self._cfg.max_age_hours * 3600:
            return self._mint_blocking().value
        if token.age_s(now) > self._cfg.refresh_after_hours * 3600 and now >= self._next_refresh_attempt:
            self._refresh_early(token)
            return (self._store.get(self._egress_id) or token).value
        return token.value

    def invalidate(self, value: str) -> None:
        log.info("token_invalidated")
        self._store.delete(self._egress_id, value)

    def current(self) -> Optional[Token]:
        return self._store.get(self._egress_id)

    def _mint(self) -> Token:
        if self._before_mint is not None:
            self._before_mint()
        token = self._chain.mint()
        self._store.put(token)
        log.info("token_minted", extra={"minter": token.minter, "egress_id": token.egress_id})
        return token

    def _mint_blocking(self) -> Token:
        deadline = self._clock() + _MINT_LOCK_TTL_S
        while True:
            with self._store.lock(self._egress_id, _MINT_LOCK_TTL_S) as mine:
                if mine:
                    existing = self._store.get(self._egress_id)  # another worker may have just minted
                    if existing is not None and existing.age_s(self._clock()) <= self._cfg.max_age_hours * 3600:
                        return existing
                    return self._mint()
            if self._clock() >= deadline:
                raise MintChainExhausted("timed out waiting for another worker to mint a token")
            self._sleep(_WAIT_FOR_OTHER_MINTER_S)
            existing = self._store.get(self._egress_id)
            if existing is not None and existing.age_s(self._clock()) <= self._cfg.max_age_hours * 3600:
                return existing

    def _refresh_early(self, old: Token) -> None:
        with self._store.lock(self._egress_id, _MINT_LOCK_TTL_S) as mine:
            if not mine:
                return  # someone else is refreshing; keep using the old token meanwhile
            current = self._store.get(self._egress_id)
            if current is not None and current.value != old.value:
                return
            try:
                self._mint()
            except (MintChainExhausted, RateLimited) as ex:
                self._next_refresh_attempt = self._clock() + _REFRESH_RETRY_AFTER_S
                log.warning("token_refresh_failed_keeping_old", extra={"error": str(ex)[:300], "age_h": round(old.age_s(self._clock()) / 3600, 1)})
