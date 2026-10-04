from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict

from app.scraper.config import ScraperConfig
from app.scraper.errors import Challenged, MintChainExhausted, RateLimited, ScraperError, Transient
from app.scraper.queue import FETCHERS, JOBS_STREAM, WORK_STREAM, Queue
from app.scraper.ratelimit import API, RateLimiter, wait_for
from app.scraper.store import ScraperStore
from app.scraper.upwork.client import UpworkSearchClient

log = logging.getLogger("scraper.fetcher")

_STALE_AFTER_INTERVALS = 3  # queued work older than this many poll intervals is dropped, not replayed
_PAUSE_AFTER_NO_TOKEN_S = 60.0
_HEARTBEAT_EVERY_S = 300.0
MAX_PAGE = 50  # the API's largest page
_DETAILS_MARGIN = 2  # extra rows requested in tier 2, in case new jobs arrived since tier 1


class Fetcher:
    """Polls one search per work item (two-tier) and emits an event for every new job.

    Tier 1 asks only for the newest job ids (tiny). Tier 2 fetches full details, and only when tier 1
    showed an id this search had not seen before.
    """

    def __init__(self, store: ScraperStore, queue: Queue, client: UpworkSearchClient, limiter: RateLimiter,
                 config: ScraperConfig, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._store = store
        self._queue = queue
        self._client = client
        self._limiter = limiter
        self._cfg = config
        self._clock = clock
        self._sleep = sleep
        self._stats = {"polls": 0, "failed": 0, "new_jobs": 0}

    def _throttle(self) -> None:
        waited = wait_for(self._limiter, API, self._cfg.rate_limit.api_per_minute, 60.0, sleep=self._sleep)
        if waited > 1:
            log.info("fetcher_throttled", extra={"waited_s": round(waited, 1)})

    def poll(self, search_id: int) -> int:
        """Poll one search. Returns how many new-job events were emitted."""
        search = self._store.get_search(search_id)
        if search is None or not search.enabled:
            return 0

        page = min(MAX_PAGE, search.ids_count or self._cfg.poller.ids_count)
        self._throttle()
        refs = self._client.search_ids(search.query_text, count=page)
        new_ids = self._store.record_hits(search_id, refs)
        self._store.record_poll_ok(search_id)

        if not search.primed:
            # First look at this search: everything is "new". Remember it, alert on nothing.
            self._store.mark_primed(search_id)
            log.info("search_primed", extra={"search_id": search_id, "jobs": len(refs)})
            return 0
        if not new_ids:
            return 0

        new_set = set(new_ids)
        deepest = max(i for i, ref in enumerate(refs) if ref.job_id in new_set)
        # A job posted between the two requests pushes everything down a row, so ask for a few more.
        count = min(page, deepest + 1 + _DETAILS_MARGIN)
        if search.primed and len(refs) == page and len(new_ids) == page:
            # Everything on the page was new: more jobs arrived since the last poll than one page holds,
            # so some may have been missed. Bigger page or shorter interval for this search.
            log.warning("page_overflow", extra={"search_id": search_id, "page": page})
        self._throttle()
        jobs = self._client.search_details(search.query_text, count=count)
        self._store.upsert_jobs(jobs)
        by_id = {job.job_id: job for job in jobs}

        # The two requests can see slightly different results. A new id that the details response lacks
        # must not stay "seen", or it would never be alerted: forget it so the next poll finds it again.
        missing = [job_id for job_id in new_ids if job_id not in by_id]
        if missing:
            self._store.forget_hits(search_id, missing)
            log.warning("new_job_missing_from_details_will_retry", extra={"search_id": search_id, "job_ids": missing})

        now = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
        max_age_s = self._cfg.poller.max_job_age_minutes * 60
        emitted = 0
        for job_id in new_ids:
            job = by_id.get(job_id)
            if job is None:
                continue
            age_s = (now - job.publish_time).total_seconds() if job.publish_time else None
            if age_s is not None and age_s > max_age_s:
                log.info("old_job_not_alerted", extra={"search_id": search_id, "job_id": job_id, "age_s": round(age_s)})
                continue
            self._queue.put(JOBS_STREAM, {"search_id": str(search_id), "job_id": job_id, "seen_at": f"{self._clock():.3f}"})
            emitted += 1
            log.info("job_new", extra={"search_id": search_id, "job_id": job_id, "title": job.title,
                                       "seen_after_publish_s": round(age_s, 1) if age_s is not None else None})
        return emitted

    def handle(self, fields: Dict[str, str]) -> None:
        """One work item, with the reaction each kind of failure calls for. Never raises."""
        try:
            search_id = int(fields["search_id"])
        except (KeyError, ValueError):
            log.warning("fetcher_bad_message", extra={"fields": fields})
            return

        try:
            age_s = self._clock() - float(fields.get("enqueued_at", self._clock()))
        except ValueError:
            age_s = 0.0
        if age_s > _STALE_AFTER_INTERVALS * self._cfg.poller.interval_s:
            log.info("fetcher_dropped_stale_work", extra={"search_id": search_id, "age_s": round(age_s)})
            return

        self._stats["polls"] += 1
        try:
            self._stats["new_jobs"] += self.poll(search_id)
            return
        except RateLimited as ex:
            backoff = ex.retry_after_s or self._cfg.failure_policy.rate_limit_backoff_s
            self._limiter.pause(API, backoff)  # every fetcher on this IP backs off, not just this one
            self._store.record_poll_error(search_id, f"rate limited: {ex}")
            log.warning("fetcher_rate_limited", extra={"search_id": search_id, "backoff_s": backoff})
        except MintChainExhausted as ex:
            self._limiter.pause(API, _PAUSE_AFTER_NO_TOKEN_S)
            self._store.record_poll_error(search_id, f"no token: {ex}")
            log.error("fetcher_no_token", extra={"search_id": search_id})
        except (Challenged, Transient, ScraperError) as ex:
            self._store.record_poll_error(search_id, f"{type(ex).__name__}: {ex}")
            log.warning("fetcher_poll_failed", extra={"search_id": search_id, "error": f"{type(ex).__name__}: {ex}"[:300]})
        except Exception as ex:  # noqa: BLE001  (a bug or DB error must not kill the worker)
            log.exception("fetcher_poll_crashed", extra={"search_id": search_id})
            try:
                self._store.record_poll_error(search_id, f"crash: {ex!r}")
            except Exception:  # noqa: BLE001
                pass
        self._stats["failed"] += 1

    def run_forever(self, consumer: str, stop: Callable[[], bool] = lambda: False,
                    on_loop: Callable[[], None] = lambda: None) -> None:
        log.info("fetcher_ready", extra={"consumer": consumer, "transport": self._client.transport.name})
        next_beat = time.monotonic() + _HEARTBEAT_EVERY_S
        queue_failures = 0
        while not stop():
            on_loop()
            if time.monotonic() >= next_beat:
                # Proof of life in the logs: on a quiet market "no new jobs" and "dead" look the same otherwise.
                log.info("fetcher_heartbeat", extra={**self._stats, "transport": self._client.transport.name})
                self._stats = {"polls": 0, "failed": 0, "new_jobs": 0}
                next_beat = time.monotonic() + _HEARTBEAT_EVERY_S
            try:
                messages = self._queue.get(WORK_STREAM, FETCHERS, consumer, count=10, block_ms=5000)
            except Exception as ex:  # noqa: BLE001  (queue backend down: retry, backing off up to a minute)
                queue_failures += 1
                wait = min(60.0, 5.0 * 2 ** (queue_failures - 1))
                if queue_failures == 1:
                    log.exception("fetcher_queue_read_failed", extra={"retry_in_s": wait})
                else:  # one traceback per outage is enough
                    log.error("fetcher_queue_read_failed", extra={"retry_in_s": wait, "consecutive": queue_failures, "error": repr(ex)[:200]})
                time.sleep(wait)
                continue
            if queue_failures:
                log.info("fetcher_queue_recovered", extra={"after_failures": queue_failures})
                queue_failures = 0
            for msg_id, fields in messages:
                self.handle(fields)
                self._queue.ack(WORK_STREAM, FETCHERS, msg_id)
