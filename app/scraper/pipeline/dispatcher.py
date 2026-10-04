from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from html import escape
from typing import Callable, Dict, List, Optional

from app.scraper.config import ScraperConfig
from app.scraper.filters import matches
from app.scraper.models import Job
from app.scraper.queue import DISPATCHERS, JOBS_STREAM, Queue
from app.scraper.store import ScraperStore

log = logging.getLogger("scraper.dispatcher")

Send = Callable[[int, str], None]  # (chat_id, text)

SENT, SHADOW, FILTERED, DUPLICATE = "sent", "shadow", "filtered", "duplicate"


def _money(value: float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s ago"
    if seconds < 7200:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def format_job(job: Job, *, description_chars: int = 300, now: Optional[datetime] = None,
               search: Optional[str] = None) -> str:
    """One alert, as Telegram HTML. Built to be read in two seconds from the notification itself:
    the title is the first line (it is what the lock screen shows), then money and level, then how fresh
    it is, then the details for whoever opens it. `search` is shown when the chat watches several."""
    if job.job_type == "hourly":
        if job.hourly_min is not None and job.hourly_max is not None:
            budget = f"Hourly {_money(job.hourly_min)}–{_money(job.hourly_max)}"
        else:
            budget = "Hourly"
    elif job.fixed_amount is not None:
        budget = f"Fixed {_money(job.fixed_amount)}"
    else:
        budget = "Fixed price"
    meta = " · ".join(x for x in [budget, job.tier.capitalize() if job.tier else None, job.duration, job.workload] if x)

    lines: List[str] = [f"<b>{escape(job.title or '(no title)')}</b>", f"💵 {escape(meta)}"]
    if job.publish_time is not None:
        now = now or datetime.now(timezone.utc)
        lines.append(f"⏱ Posted {_ago((now - job.publish_time).total_seconds())}")
    if search:
        lines.append(f"🔎 {escape(search)}")
    if description_chars and job.description:
        d = " ".join(job.description.split())  # one paragraph: the excerpt is a preview, not the posting
        lines.append("")
        lines.append(escape(d if len(d) <= description_chars else d[:description_chars].rstrip() + "…"))
    if job.skills:
        lines.append("")
        lines.append("<i>" + escape(", ".join(job.skills[:10])) + "</i>")
    lines.append("")
    lines.append(f'<a href="{escape(job.url, quote=True)}">Open on Upwork →</a>')
    return "\n".join(lines)


class Dispatcher:
    """Turns a new-job event into Telegram messages for the chats subscribed to that search.

    A delivery row is written before sending, so a job is never alerted twice to the same subscription
    or twice to the same chat through two of its searches
    (at-most-once; the Telegram notifier retries network errors itself).
    In `shadow` mode nothing goes to subscribers: the would-be alert is logged, and copied to
    `admin_chat_id` (tagged) if one is configured.
    """

    def __init__(self, store: ScraperStore, queue: Queue, config: ScraperConfig, send: Send) -> None:
        self._store = store
        self._queue = queue
        self._cfg = config
        self._send = send

    def dispatch(self, search_id: int, job_id: str) -> Dict[str, int]:
        counts = {SENT: 0, SHADOW: 0, FILTERED: 0, DUPLICATE: 0}
        job = self._store.get_job(job_id)
        if job is None:
            log.warning("dispatch_job_missing", extra={"search_id": search_id, "job_id": job_id})
            return counts

        live = self._cfg.dispatcher.mode == "live"
        search_text = None
        for sub in self._store.subscriptions_for_search(search_id):
            if not matches(job, sub.include_words, sub.exclude_words):
                if self._store.try_mark_delivered(sub.subscription_id, job_id, FILTERED):
                    counts[FILTERED] += 1
                    log.info("job_filtered_out", extra={"subscription_id": sub.subscription_id, "job_id": job_id, "title": job.title})
                continue
            if self._store.chat_already_has_job(sub.chat_id, job_id):
                # Same chat, another of its searches matched this job too: tell a person once.
                if self._store.try_mark_delivered(sub.subscription_id, job_id, DUPLICATE):
                    counts[DUPLICATE] += 1
                continue
            status = SENT if live else SHADOW
            if not self._store.try_mark_delivered(sub.subscription_id, job_id, status):
                continue  # this subscription already has this job (e.g. the event was redelivered)
            counts[status] += 1
            # Say which search matched only when the chat has more than one; otherwise it is noise.
            several = len(self._store.subscriptions_for_chat(sub.chat_id)) > 1
            if several and search_text is None:
                spec = self._store.get_search(search_id)
                search_text = spec.query_text if spec else None
            text = format_job(job, description_chars=self._cfg.dispatcher.show_description_chars,
                              search=search_text if several else None)
            tag = escape(self._cfg.dispatcher.live_tag)
            if live:
                self._send(sub.chat_id, tag + text)
            elif self._cfg.dispatcher.admin_chat_id is not None:
                self._send(self._cfg.dispatcher.admin_chat_id, f"🧪 [shadow → chat {sub.chat_id}]\n\n" + text)
            log.info("job_delivered" if live else "job_would_deliver",
                     extra={"subscription_id": sub.subscription_id, "chat_id": sub.chat_id, "job_id": job_id, "title": job.title})
        return counts

    def handle(self, fields: Dict[str, str]) -> None:
        try:
            self.dispatch(int(fields["search_id"]), fields["job_id"])
        except (KeyError, ValueError):
            log.warning("dispatcher_bad_message", extra={"fields": fields})
        except Exception:  # noqa: BLE001  (a DB error must not kill the worker)
            log.exception("dispatcher_failed", extra={"fields": fields})

    def run_forever(self, consumer: str, stop: Callable[[], bool] = lambda: False,
                    on_loop: Callable[[], None] = lambda: None) -> None:
        log.info("dispatcher_ready", extra={"consumer": consumer, "mode": self._cfg.dispatcher.mode})
        queue_failures = 0
        while not stop():
            on_loop()
            try:
                messages = self._queue.get(JOBS_STREAM, DISPATCHERS, consumer, count=20, block_ms=5000)
            except Exception as ex:  # noqa: BLE001  (queue backend down: retry, backing off up to a minute)
                queue_failures += 1
                wait = min(60.0, 5.0 * 2 ** (queue_failures - 1))
                if queue_failures == 1:
                    log.exception("dispatcher_queue_read_failed", extra={"retry_in_s": wait})
                else:  # one traceback per outage is enough
                    log.error("dispatcher_queue_read_failed", extra={"retry_in_s": wait, "consecutive": queue_failures, "error": repr(ex)[:200]})
                time.sleep(wait)
                continue
            if queue_failures:
                log.info("dispatcher_queue_recovered", extra={"after_failures": queue_failures})
                queue_failures = 0
            for msg_id, fields in messages:
                self.handle(fields)
                self._queue.ack(JOBS_STREAM, DISPATCHERS, msg_id)
