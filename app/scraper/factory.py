"""Builds the object graph from the config. The only place that knows which concrete classes exist."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from app.scraper.config import ScraperConfig
from app.scraper.tokens.chain import MinterChain
from app.scraper.tokens.manager import TokenManager
from app.scraper.tokens.minters import HttpMinter, Minter, SubprocessMinter
from app.scraper.tokens.store import MemoryTokenStore, RedisTokenStore, TokenStore
from app.scraper.transports import Transport, transport_from_config
from app.scraper.upwork.client import UpworkSearchClient


def redis_client():
    from app.queue.redis_streams import get_redis_client
    return get_redis_client()


def build_minters(config: ScraperConfig) -> List[Minter]:
    transports: Dict[str, Transport] = {}
    minters: List[Minter] = []
    for m in config.minters:
        if m.kind == "http":
            transport = transports.setdefault(m.transport, transport_from_config(config, m.transport))
            minters.append(HttpMinter(m.name, transport, config.upwork))
        else:
            minters.append(SubprocessMinter(m, config.upwork))
    return minters


def build_token_store(config: ScraperConfig, redis=None) -> TokenStore:
    if config.backend == "memory":
        return MemoryTokenStore()
    return RedisTokenStore(redis or redis_client())


def build_token_manager(config: ScraperConfig, store: TokenStore, *, before_mint: Optional[Callable[[], None]] = None) -> TokenManager:
    chain = MinterChain(build_minters(config), config.failure_policy, egress_id=config.egress_id)
    return TokenManager(store, chain, config.tokens, egress_id=config.egress_id, before_mint=before_mint)


def build_search_client(config: ScraperConfig, tokens: TokenManager) -> UpworkSearchClient:
    names = [config.poller.transport] + ([config.poller.fallback_transport] if config.poller.fallback_transport else [])
    return UpworkSearchClient(config, [transport_from_config(config, n) for n in names], tokens)


# --- shared backends and the full runtime ---------------------------------------------------------

def build_queue(config: ScraperConfig, redis=None):
    from app.scraper.queue import MemoryQueue, RedisQueue
    return MemoryQueue() if config.backend == "memory" else RedisQueue(redis or redis_client())


def build_limiter(config: ScraperConfig, redis=None):
    from app.scraper.ratelimit import MemoryRateLimiter, RedisRateLimiter
    if config.backend == "memory":
        return MemoryRateLimiter()
    return RedisRateLimiter(redis or redis_client(), egress_id=config.egress_id)


def build_store():
    from app.scraper.store import ScraperStore
    from app.store.db import build_db_dsn_from_env, make_engine
    return ScraperStore(make_engine(build_db_dsn_from_env()))


class Runtime:
    """Everything a role needs, built once per process. With `backend: memory` the same Runtime must be
    shared by all roles (they run as threads); with Redis each process builds its own."""

    def __init__(self, config: ScraperConfig) -> None:
        from app.scraper.ratelimit import PAGE, wait_for

        self.config = config
        self.redis = redis_client() if config.backend == "redis" else None
        self.store = build_store()
        self.queue = build_queue(config, self.redis)
        self.limiter = build_limiter(config, self.redis)
        self.token_store = build_token_store(config, self.redis)
        # Minting loads the search page, which has its own (stricter) per-IP limit.
        self.tokens = build_token_manager(
            config, self.token_store,
            before_mint=lambda: wait_for(self.limiter, PAGE, config.rate_limit.page_per_10min, 600.0),
        )

    def search_client(self) -> UpworkSearchClient:
        return build_search_client(self.config, self.tokens)
