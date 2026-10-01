from __future__ import annotations

import random
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.scraper.config import ScraperConfig
from app.scraper.errors import Challenged, MintChainExhausted, RateLimited, Transient
from app.scraper.filters import matches
from app.scraper.models import Job
from app.scraper.pipeline.dispatcher import Dispatcher, format_job
from app.scraper.pipeline.fetcher import Fetcher
from app.scraper.pipeline.scheduler import Scheduler
from app.scraper.queue import DISPATCHERS, FETCHERS, JOBS_STREAM, WORK_STREAM, MemoryQueue
from app.scraper.ratelimit import API, MemoryRateLimiter, wait_for
from app.scraper.upwork.parse import parse_jobs

from .conftest import fixture_text

T0 = 1_800_000_000.0


def job(job_id="~j1", *, title="Python scraper", description="Scrape a site with Python", skills=("Python", "Data Scraping"),
        published_s_ago=30.0, **kw) -> Job:
    pub = datetime.fromtimestamp(T0 - published_s_ago, tz=timezone.utc)
    base = dict(job_id=job_id, url=f"https://www.upwork.com/jobs/{job_id}", title=title, description=description,
                skills=tuple(skills), job_type="fixed", fixed_amount=500.0, hourly_min=None, hourly_max=None,
                tier="expert", duration="1 to 3 months", workload=None, create_time=pub, publish_time=pub)
    base.update(kw)
    return Job(**base)


class FakeClient:
    """Scripted search client: `results` is what Upwork currently returns, newest first."""

    transport = SimpleNamespace(name="fake")

    def __init__(self, results=()):
        self.results = list(results)
        self.ids_calls = 0
        self.details_calls = []
        self.fail_with = None

    def search_ids(self, query_text):
        self.ids_calls += 1
        if self.fail_with:
            raise self.fail_with
        return [j.ref for j in self.results]

    def search_details(self, query_text, count):
        self.details_calls.append(count)
        hidden, self.hide_from_details_once = getattr(self, "hide_from_details_once", set()), set()
        return [j for j in self.results[:count] if j.job_id not in hidden]


@pytest.fixture
def cfg(config_dict):
    return ScraperConfig.model_validate(config_dict)


@pytest.fixture
def rig(store, cfg):
    """Everything wired with a controllable clock."""
    clock = SimpleNamespace(now=T0)
    queue, limiter, client = MemoryQueue(), MemoryRateLimiter(clock=lambda: clock.now), FakeClient()
    fetcher = Fetcher(store, queue, client, limiter, cfg, clock=lambda: clock.now, sleep=lambda s: setattr(clock, "now", clock.now + s))
    sent = []
    dispatcher = Dispatcher(store, queue, cfg, send=lambda chat, text: sent.append((chat, text)))
    scheduler = Scheduler(store, queue, cfg, clock=lambda: clock.now, rng=random.Random(1))
    return SimpleNamespace(store=store, queue=queue, limiter=limiter, client=client, fetcher=fetcher,
                           dispatcher=dispatcher, scheduler=scheduler, sent=sent, clock=clock, cfg=cfg)


def drain(queue, stream, group):
    out = []
    while True:
        batch = queue.get(stream, group, "t", count=50, block_ms=0)
        if not batch:
            return out
        for msg_id, fields in batch:
            queue.ack(stream, group, msg_id)
            out.append(fields)


# --- filters ----------------------------------------------------------------------------------

@pytest.mark.parametrize("include, exclude, expected", [
    ((), (), True),
    (("python",), (), True),
    (("PYTHON",), (), True),                       # case-insensitive
    (("java",), (), False),
    (("java", "data scraping"), (), True),         # any include word; phrases work; skills are searched
    ((), ("python",), False),
    (("python",), ("scrape",), False),             # exclude wins
    (("scrap",), (), False),                       # whole words only: "scrap" is not "scrape"/"scraping"
])
def test_filter_matches(include, exclude, expected):
    assert matches(job(), include, exclude) is expected


def test_filter_drops_the_alloy_scrap_attorney_job_but_keeps_real_scraping():
    attorney = job(title="Hungarian attorney: legal opinion on recycling of forint coins",
                   description="Shipment of the resulting copper, nickel and zinc alloy scrap within the EU.",
                   skills=("International Law", "Compliance"))
    dentist = job(title="Manhattan Dentist Email Research", description="Collect emails from directories.",
                  skills=("Data Scraping", "Data Entry"))
    words = ("python", "scraping", "scraper", "scrape")
    assert matches(attorney, words) is False and matches(dentist, words) is True


def test_filter_handles_words_with_symbols():
    assert matches(job(description="Need a C++ and .NET developer"), ("c++",)) is True
    assert matches(job(description="Need a C developer"), ("c++",)) is False


# --- rate limiter / queue ---------------------------------------------------------------------

def test_memory_rate_limiter_window_and_pause():
    clock = SimpleNamespace(now=0.0)
    rl = MemoryRateLimiter(clock=lambda: clock.now)
    assert [rl.acquire(API, 2, 60) for _ in range(2)] == [0.0, 0.0]
    assert rl.acquire(API, 2, 60) == pytest.approx(60.0)
    clock.now = 61
    assert rl.acquire(API, 2, 60) == 0.0
    rl.pause(API, 120)
    assert rl.acquire(API, 2, 60) == pytest.approx(120.0)
    clock.now = 182
    assert rl.acquire(API, 2, 60) == 0.0


def test_wait_for_sleeps_until_allowed():
    clock = SimpleNamespace(now=0.0)
    rl = MemoryRateLimiter(clock=lambda: clock.now)
    rl.acquire(API, 1, 10)
    waited = wait_for(rl, API, 1, 10, sleep=lambda s: setattr(clock, "now", clock.now + s))
    assert 10 <= waited < 11


def test_memory_queue_blocks_then_delivers_and_tracks_backlog():
    q = MemoryQueue()
    assert q.get("s", "g", "c", block_ms=10) == []
    threading.Timer(0.05, lambda: q.put("s", {"a": "1"})).start()
    (msg_id, fields), = q.get("s", "g", "c", block_ms=2000)
    assert fields == {"a": "1"} and q.backlog("s", "g") == 1      # delivered but not acked
    q.ack("s", "g", msg_id)
    assert q.backlog("s", "g") == 0


# --- scheduler --------------------------------------------------------------------------------

def test_scheduler_enqueues_due_searches_once_per_interval(rig):
    s = rig.store.upsert_search("python")
    rig.store.subscribe(111, s)
    assert rig.scheduler.tick() == 1 and rig.scheduler.tick() == 0          # rescheduled into the future
    (fields,) = drain(rig.queue, WORK_STREAM, FETCHERS)
    assert fields["search_id"] == str(s) and float(fields["enqueued_at"]) == T0


def test_scheduler_interval_jitters_and_stretches_under_load(rig):
    search = SimpleNamespace(poll_seconds=None)
    few = [rig.scheduler.interval_for(search, 5) for _ in range(200)]
    assert 21 <= min(few) and max(few) <= 39 and len({round(x, 3) for x in few}) > 100     # 30 s +/- 30 %
    # 200 searches * 1.2 calls / 60 per minute -> 240 s floor: the budget, not the config, sets the pace
    many = [rig.scheduler.interval_for(search, 200) for _ in range(200)]
    assert 168 <= min(many) and max(many) <= 312
    assert 42 <= rig.scheduler.interval_for(SimpleNamespace(poll_seconds=60), 5) <= 78      # per-search override


# --- fetcher ----------------------------------------------------------------------------------

def test_first_poll_primes_without_events_or_detail_fetch(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a"), job("~b")]
    assert rig.fetcher.poll(s) == 0
    assert rig.client.details_calls == [] and drain(rig.queue, JOBS_STREAM, DISPATCHERS) == []
    assert rig.store.get_search(s).primed is True


def test_no_new_ids_means_one_light_call_only(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a")]
    rig.fetcher.poll(s)
    rig.fetcher.poll(s)
    assert rig.client.ids_calls == 2 and rig.client.details_calls == []


def test_new_job_triggers_details_for_just_enough_rows_and_one_event(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a"), job("~b")]
    rig.fetcher.poll(s)
    rig.client.results = [job("~new"), job("~a"), job("~b")]
    assert rig.fetcher.poll(s) == 1
    assert rig.client.details_calls == [3]                                    # position 0, plus a margin of 2
    assert [f["job_id"] for f in drain(rig.queue, JOBS_STREAM, DISPATCHERS)] == ["~new"]
    assert rig.store.get_job("~new").title == "Python scraper"
    assert rig.fetcher.poll(s) == 0                                           # not new the second time


def test_two_new_jobs_between_polls_are_both_emitted(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a")]
    rig.fetcher.poll(s)
    rig.client.results = [job("~n2"), job("~n1"), job("~a")]
    assert rig.fetcher.poll(s) == 2 and rig.client.details_calls == [4]


def test_details_request_never_exceeds_the_ids_page(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job(f"~old{i}") for i in range(10)]
    rig.fetcher.poll(s)
    rig.client.results = [job(f"~new{i}") for i in range(10)]                # a whole page of new jobs
    assert rig.fetcher.poll(s) == 10 and rig.client.details_calls == [10]     # capped at poller.ids_count


def test_job_missing_from_details_is_retried_on_the_next_poll_not_lost(rig):
    # Seen live on 2026-10-01: tier 1 listed a job, tier 2 a moment later did not, and the alert was lost.
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a")]
    rig.fetcher.poll(s)
    rig.client.results = [job("~flaky"), job("~solid"), job("~a")]
    rig.client.hide_from_details_once = {"~flaky"}
    assert rig.fetcher.poll(s) == 1                                           # only ~solid this time
    assert [f["job_id"] for f in drain(rig.queue, JOBS_STREAM, DISPATCHERS)] == ["~solid"]
    assert rig.fetcher.poll(s) == 1                                           # ~flaky is found again and alerted
    assert [f["job_id"] for f in drain(rig.queue, JOBS_STREAM, DISPATCHERS)] == ["~flaky"]
    assert rig.fetcher.poll(s) == 0                                           # and only once


def test_old_job_is_stored_but_not_alerted(rig):
    s = rig.store.upsert_search("python")
    rig.client.results = [job("~a")]
    rig.fetcher.poll(s)
    rig.client.results = [job("~old", published_s_ago=3600), job("~a")]      # e.g. a renewed posting
    assert rig.fetcher.poll(s) == 0
    assert rig.store.get_job("~old") is not None and drain(rig.queue, JOBS_STREAM, DISPATCHERS) == []


def test_rate_limit_pauses_everyone_and_is_recorded(rig):
    s = rig.store.upsert_search("python")
    rig.store.subscribe(111, s)
    rig.client.fail_with = RateLimited("429", retry_after_s=90)
    rig.fetcher.handle({"search_id": str(s), "enqueued_at": str(T0)})
    assert rig.limiter.acquire(API, 60, 60) == pytest.approx(90.0)
    snap = rig.store.snapshot()["searches"][0]
    assert snap["consecutive_failures"] == 1 and "rate limited" in snap["last_error"]


@pytest.mark.parametrize("error", [Challenged("403"), Transient("503"), MintChainExhausted("all failed"), RuntimeError("bug")])
def test_handle_never_raises_and_records_the_error(rig, error):
    s = rig.store.upsert_search("python")
    rig.client.fail_with = error
    rig.fetcher.handle({"search_id": str(s), "enqueued_at": str(T0)})
    assert rig.store.snapshot()["searches"][0]["consecutive_failures"] == 1


def test_stale_work_is_dropped_not_replayed(rig):
    s = rig.store.upsert_search("python")
    rig.fetcher.handle({"search_id": str(s), "enqueued_at": str(T0 - 600)})  # queued 10 min ago (fetcher was down)
    assert rig.client.ids_calls == 0


def test_bad_messages_are_ignored(rig):
    rig.fetcher.handle({"nope": "1"})
    rig.dispatcher.handle({"search_id": "x"})


# --- dispatcher -------------------------------------------------------------------------------

def test_format_job_variants():
    fixed = format_job(job(), description_chars=10)
    assert fixed.splitlines()[0] == "• Python scraper — Fixed $500 | Expert | 1 to 3 months"
    assert "Scrape a s…" in fixed and "https://www.upwork.com/jobs/~j1" in fixed and "skills: Python, Data Scraping" in fixed
    hourly = format_job(job(job_type="hourly", fixed_amount=None, hourly_min=25.0, hourly_max=65.5, workload="Less than 30 hrs/week"))
    assert "Hourly $25-$65.50" in hourly and "Less than 30 hrs/week" in hourly
    assert "Hourly |" in format_job(job(job_type="hourly", fixed_amount=None))
    assert "…" not in format_job(job(), description_chars=0) and "Scrape a site" not in format_job(job(), description_chars=0)


def test_live_dispatch_fans_out_filters_and_never_repeats(rig, cfg):
    live = cfg.model_copy(update={"dispatcher": cfg.dispatcher.model_copy(update={"mode": "live"})})
    d = Dispatcher(rig.store, rig.queue, live, send=lambda chat, text: rig.sent.append((chat, text)))
    s = rig.store.upsert_search("python")
    rig.store.subscribe(111, s)
    rig.store.subscribe(222, s, include_words=["java"])                       # filtered out
    rig.store.subscribe(333, s, exclude_words=["nothing-matching"])
    rig.store.upsert_jobs([job("~x")])
    assert d.dispatch(s, "~x") == {"sent": 2, "shadow": 0, "filtered": 1, "duplicate": 0}
    assert [chat for chat, _ in rig.sent] == [111, 333] and rig.sent[0][1].startswith("🔔 New job")
    assert sum(d.dispatch(s, "~x").values()) == 0                            # redelivered event: no duplicates
    assert len(rig.sent) == 2


def test_shadow_mode_sends_nothing_to_subscribers(rig, cfg):
    s = rig.store.upsert_search("python")
    rig.store.subscribe(111, s)
    rig.store.upsert_jobs([job("~x")])
    assert rig.dispatcher.dispatch(s, "~x") == {"sent": 0, "shadow": 1, "filtered": 0, "duplicate": 0} and rig.sent == []
    with_admin = cfg.model_copy(update={"dispatcher": cfg.dispatcher.model_copy(update={"admin_chat_id": 999})})
    d = Dispatcher(rig.store, rig.queue, with_admin, send=lambda chat, text: rig.sent.append((chat, text)))
    rig.store.upsert_jobs([job("~y")])
    d.dispatch(s, "~y")
    assert rig.sent[0][0] == 999 and "[shadow → chat 111]" in rig.sent[0][1]


def test_dispatch_of_unknown_job_is_harmless(rig):
    assert sum(rig.dispatcher.dispatch(1, "~missing").values()) == 0


# --- end to end -------------------------------------------------------------------------------

def test_end_to_end_two_searches_two_chats(rig, cfg):
    live = cfg.model_copy(update={"dispatcher": cfg.dispatcher.model_copy(update={"mode": "live"})})
    dispatcher = Dispatcher(rig.store, rig.queue, live, send=lambda chat, text: rig.sent.append((chat, text)))
    py, react = rig.store.upsert_search("python"), rig.store.upsert_search("react")
    rig.store.subscribe(111, py)
    rig.store.subscribe(222, py)
    rig.store.subscribe(222, react)

    def run_cycle(results):
        rig.client.results = results
        rig.scheduler.tick()
        for fields in drain(rig.queue, WORK_STREAM, FETCHERS):
            rig.fetcher.handle(fields)
        for fields in drain(rig.queue, JOBS_STREAM, DISPATCHERS):
            dispatcher.handle(fields)

    run_cycle([job("~a")])                                    # both searches prime; nobody is alerted
    assert rig.sent == []
    for sid in (py, react):
        rig.store.schedule_next(sid, -1)
    run_cycle([job("~b"), job("~a")])                         # ~b is new for both searches
    # chat 222 holds both searches and both matched ~b, but a person is told once
    assert sorted(chat for chat, _ in rig.sent) == [111, 222]
    snap = rig.store.snapshot()
    assert snap["deliveries_last_hour"] == {"sent": 2, "duplicate": 1} and all(s["primed"] and s["consecutive_failures"] == 0 for s in snap["searches"])


def test_real_fixture_flows_through_store_and_format(rig):
    jobs = parse_jobs(fixture_text("search_details.json"), job_url_template="https://www.upwork.com/jobs/{job_id}")
    rig.store.upsert_jobs(jobs)
    for j in jobs:
        text = format_job(rig.store.get_job(j.job_id))
        assert j.url in text and (j.title or "") in text


# --- queue backend outages --------------------------------------------------------------------

class FlakyQueue(MemoryQueue):
    """Raises on the first `failures` reads, then behaves normally."""

    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def get(self, *args, **kwargs):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("redis down")
        return super().get(*args, **kwargs)


@pytest.mark.parametrize("role", ["fetcher", "dispatcher"])
def test_queue_outage_backs_off_then_recovers(rig, cfg, role, monkeypatch, caplog):
    queue = FlakyQueue(failures=5)
    sleeps = []
    module = __import__(f"app.scraper.pipeline.{role}", fromlist=["time"])
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    worker = (Fetcher(rig.store, queue, rig.client, rig.limiter, cfg) if role == "fetcher"
              else Dispatcher(rig.store, queue, cfg, send=lambda chat, text: None))
    reads = iter(range(7))                                       # 5 failures, 1 good read, then stop
    with caplog.at_level("INFO"):
        worker.run_forever("c", stop=lambda: next(reads, None) is None or (queue.failures == 0 and len(sleeps) == 5 and any("recovered" in r.message for r in caplog.records)))
    assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0]               # doubles, capped at a minute
    failed = [r for r in caplog.records if r.message == f"{role}_queue_read_failed"]
    assert len(failed) == 5 and sum(1 for r in failed if r.exc_info) == 1   # one traceback per outage
    assert any(r.message == f"{role}_queue_recovered" for r in caplog.records)
