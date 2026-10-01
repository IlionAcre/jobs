"""Runs against the real Postgres from .env, inside a throwaway schema that is dropped afterwards."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.scraper.models import JobRef
from app.scraper.store import normalize_query
from app.scraper.upwork.parse import parse_jobs

from .conftest import fixture_text

NOW = datetime.now(timezone.utc)


def refs(*ids):
    return [JobRef(i, NOW) for i in ids]


def test_normalize_query():
    assert normalize_query("  Python   OR  Scraping ") == "python or scraping"


def test_same_search_text_is_one_search(store):
    a = store.upsert_search("python OR scraping")
    b = store.upsert_search("  Python or   SCRAPING ")
    c = store.upsert_search("react")
    assert a == b and c != a and store.count_enabled_searches() == 2


def test_only_subscribed_due_searches_are_returned(store):
    with_sub = store.upsert_search("python")
    store.upsert_search("orphan")                      # nobody subscribed -> never polled
    store.subscribe(111, with_sub)
    assert [s.search_id for s in store.due_searches()] == [with_sub]
    store.schedule_next(with_sub, 3600)
    assert store.due_searches() == []
    store.schedule_next(with_sub, -1)
    due = store.due_searches()
    assert due[0].query_text == "python" and due[0].primed is False


def test_record_hits_returns_only_new_ids_in_order(store):
    s = store.upsert_search("python")
    assert store.record_hits(s, refs("~a", "~b", "~c")) == ["~a", "~b", "~c"]
    assert store.record_hits(s, refs("~d", "~a", "~e")) == ["~d", "~e"]
    assert store.record_hits(s, refs("~a")) == [] and store.record_hits(s, []) == []
    other = store.upsert_search("react")               # hits are per search
    assert store.record_hits(other, refs("~a")) == ["~a"]


def test_jobs_round_trip_and_update(store):
    jobs = parse_jobs(fixture_text("search_details.json"), job_url_template="https://www.upwork.com/jobs/{job_id}")
    store.upsert_jobs(jobs)
    store.upsert_jobs(jobs)                            # idempotent
    got = store.get_job(jobs[1].job_id)
    assert got == jobs[1]                              # `raw` is excluded from equality
    assert (got.hourly_min, got.hourly_max, got.skills) == (jobs[1].hourly_min, jobs[1].hourly_max, jobs[1].skills)
    assert store.get_job("~nope") is None


def test_subscription_filters_and_resubscribe(store):
    s = store.upsert_search("python")
    sub = store.subscribe(111, s, include_words=["python"], exclude_words=["scrap"])
    assert store.subscribe(111, s, include_words=["django"]) == sub     # same chat+search -> updated
    store.subscribe(222, s)
    rows = store.subscriptions_for_search(s)
    assert [(r.chat_id, r.include_words, r.exclude_words) for r in rows] == [(111, ("django",), ()), (222, (), ())]


def test_delivery_is_recorded_once_per_subscription(store):
    s = store.upsert_search("python")
    a, b = store.subscribe(111, s), store.subscribe(222, s)
    assert store.try_mark_delivered(a, "~j", "sent") is True
    assert store.try_mark_delivered(a, "~j", "sent") is False
    assert store.try_mark_delivered(b, "~j", "shadow") is True


def test_poll_bookkeeping_and_snapshot(store):
    s = store.upsert_search("python")
    store.subscribe(111, s)
    store.record_poll_error(s, "boom")
    store.record_poll_error(s, "boom again")
    snap = store.snapshot()["searches"][0]
    assert snap["consecutive_failures"] == 2 and snap["last_error"] == "boom again" and snap["subscribers"] == 1
    store.record_poll_ok(s)
    store.mark_primed(s)
    snap = store.snapshot()["searches"][0]
    assert snap["consecutive_failures"] == 0 and snap["primed"] is True and snap["last_ok_at"] is not None
    assert store.get_search(s).primed is True


def test_cleanup_removes_only_old_rows(store, engine, schema):
    s = store.upsert_search("python")
    sub = store.subscribe(111, s)
    jobs = parse_jobs(fixture_text("search_details.json"), job_url_template="https://www.upwork.com/jobs/{job_id}")
    store.upsert_jobs(jobs)
    store.record_hits(s, [j.ref for j in jobs])
    store.try_mark_delivered(sub, jobs[0].job_id, "sent")
    store.try_mark_delivered(sub, jobs[1].job_id, "sent")
    old = NOW - timedelta(days=45)
    with engine.begin() as con:
        con.execute(text(f'UPDATE "{schema}".jobs SET last_seen_at = :t WHERE job_id = :j'), {"t": old, "j": jobs[0].job_id})
        con.execute(text(f'UPDATE "{schema}".search_hits SET first_seen_at = :t WHERE job_id = :j'), {"t": old, "j": jobs[0].job_id})
        con.execute(text(f'UPDATE "{schema}".deliveries SET delivered_at = :t WHERE job_id = :j'), {"t": old, "j": jobs[0].job_id})
    assert store.cleanup(30) == {"deliveries": 1, "search_hits": 1, "jobs": 1}
    assert store.get_job(jobs[0].job_id) is None and store.get_job(jobs[1].job_id) is not None
    assert store.cleanup(30) == {"deliveries": 0, "search_hits": 0, "jobs": 0}
