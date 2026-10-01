from __future__ import annotations

import logging
import random
import time
from typing import Callable

from app.scraper.config import ScraperConfig
from app.scraper.queue import WORK_STREAM, Queue
from app.scraper.store import ScraperStore, SearchRow

log = logging.getLogger("scraper.scheduler")

_TICK_S = 2.0
# A poll is one light request, plus a second one only when something is new.
_CALLS_PER_POLL = 1.2
_CLEANUP_EVERY_S = 24 * 3600.0


class Scheduler:
    """Decides which searches are due and puts them on the work queue.

    Postgres is the source of truth for the schedule (`next_run_at`), so the scheduler holds no state and
    can be restarted freely. Run exactly one; fetchers are what you scale.
    """

    def __init__(self, store: ScraperStore, queue: Queue, config: ScraperConfig, *,
                 clock: Callable[[], float] = time.time, rng: random.Random | None = None) -> None:
        self._store = store
        self._queue = queue
        self._cfg = config
        self._clock = clock
        self._rng = rng or random.Random()

    def interval_for(self, search: SearchRow, enabled_searches: int) -> float:
        """Seconds until this search runs again.

        Stretches automatically when there are more searches than the IP's request budget can serve at
        the configured interval, instead of bursting into a rate limit.
        """
        base = float(search.poll_seconds or self._cfg.poller.interval_s)
        budget_floor = enabled_searches * _CALLS_PER_POLL * 60.0 / self._cfg.rate_limit.api_per_minute
        interval = max(base, budget_floor)
        jitter = self._cfg.poller.jitter
        return interval * self._rng.uniform(1 - jitter, 1 + jitter)

    def tick(self) -> int:
        due = self._store.due_searches()
        if not due:
            return 0
        enabled = self._store.count_enabled_searches()
        for search in due:
            self._queue.put(WORK_STREAM, {"search_id": str(search.search_id), "enqueued_at": f"{self._clock():.3f}"})
            self._store.schedule_next(search.search_id, self.interval_for(search, enabled))
        log.debug("scheduler_enqueued", extra={"count": len(due)})
        return len(due)

    def run_forever(self, stop: Callable[[], bool] = lambda: False, on_loop: Callable[[], None] = lambda: None) -> None:
        log.info("scheduler_ready", extra={"interval_s": self._cfg.poller.interval_s})
        next_cleanup = time.monotonic() + 300  # not at startup: let the first polls go first
        while not stop():
            on_loop()
            try:
                self.tick()
                if time.monotonic() >= next_cleanup:
                    next_cleanup = time.monotonic() + _CLEANUP_EVERY_S
                    log.info("retention_cleanup", extra={"retention_days": self._cfg.retention_days,
                                                         "deleted": self._store.cleanup(self._cfg.retention_days)})
            except Exception:  # noqa: BLE001  (DB/queue hiccup: log and keep going)
                log.exception("scheduler_tick_failed")
            time.sleep(_TICK_S)
