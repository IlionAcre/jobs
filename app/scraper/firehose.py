"""
The all-jobs experiment: one search with an empty query, polled often, with no subscribers.

It collects every new Upwork job (full details, into scraper.jobs) without alerting anyone, next to the
normal per-search polling. `report()` then answers three questions from the data:

  speed         how soon after publishing each method saw a job
  completeness  did the collector see every job the per-search polling found (and when)
  granularity   matching the collected jobs locally: what would we catch, miss or add compared with
                Upwork's own search for the same query

Read-only apart from the collector itself, which is just a search row (`always_poll`, larger page).
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Engine, text

from app.scraper.query_match import job_text, matches_query
from app.scraper.store import ScraperStore, normalize_query

FIREHOSE_QUERY = ""


def _dist(xs: List[float]) -> Dict[str, Any]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    pick = lambda q: round(xs[min(len(xs) - 1, int(q * (len(xs) - 1)))], 1)  # noqa: E731
    return {"n": len(xs), "min_s": round(xs[0], 1), "p10_s": pick(0.1), "median_s": round(statistics.median(xs), 1),
            "p90_s": pick(0.9), "max_s": round(xs[-1], 1)}


def report(engine: Engine, store: ScraperStore, *, query: str, hours: float, schema: str = "scraper") -> Dict[str, Any]:
    s = f'"{schema}"'
    with engine.begin() as con:
        fh = con.execute(text(f"SELECT search_id, primed_at FROM {s}.searches WHERE query_norm = :q"),
                         {"q": normalize_query(FIREHOSE_QUERY)}).first()
        qs = con.execute(text(f"SELECT search_id, primed_at FROM {s}.searches WHERE query_norm = :q"),
                         {"q": normalize_query(query)}).first()
        if fh is None or fh[1] is None:
            return {"error": "the all-jobs collector is not running yet (python main.py scraper firehose --on)"}
        if qs is None or qs[1] is None:
            return {"error": f"no primed search for {query!r}"}
        since = con.execute(text("SELECT GREATEST(NOW() - make_interval(secs => :h), :a, :b) + interval '5 seconds'"),
                            {"h": hours * 3600, "a": fh[1], "b": qs[1]}).scalar_one()
        # Stop a little before now: a job one side just saw may still be on its way to the other.
        until = con.execute(text("SELECT NOW() - interval '3 minutes'")).scalar_one()
        rows = lambda sid: {r[0]: (r[1], r[2]) for r in con.execute(text(  # noqa: E731
            f"SELECT job_id, first_seen_at, publish_time FROM {s}.search_hits "
            f"WHERE search_id = :id AND first_seen_at BETWEEN :a AND :b"), {"id": sid, "a": since, "b": until})}
        fire, search = rows(fh[0]), rows(qs[0])
        polls = con.execute(text(f"""
            SELECT count(*) AS jobs, date_trunc('second', first_seen_at) AS poll FROM {s}.search_hits
            WHERE search_id = :id AND first_seen_at BETWEEN :a AND :b GROUP BY 2 ORDER BY 1 DESC LIMIT 1"""),
                            {"id": fh[0], "a": since, "b": until}).first()
        hourly = con.execute(text(f"""
            SELECT date_trunc('hour', first_seen_at) AS h, count(*) FROM {s}.search_hits
            WHERE search_id = :id AND first_seen_at BETWEEN :a AND :b GROUP BY 1 ORDER BY 1"""),
                             {"id": fh[0], "a": since, "b": until}).all()

    lag = lambda d: [(seen - pub).total_seconds() for seen, pub in d.values() if pub is not None]  # noqa: E731
    found_by_search = set(search)
    both = found_by_search & set(fire)
    missed_by_firehose = sorted(found_by_search - set(fire))
    firehose_lead = [(search[j][0] - fire[j][0]).total_seconds() for j in both]  # > 0: collector saw it first

    # Granularity: our literal matcher over every collected job vs what Upwork's search returned.
    local = set()
    no_details = 0
    for job_id in fire:
        job = store.get_job(job_id)
        if job is None or job.description is None:
            no_details += 1
            continue
        if matches_query(query, job_text(job)):
            local.add(job_id)
    upwork = found_by_search & set(fire)  # only jobs both could have seen
    titles = lambda ids: [{"job_id": j, "title": (store.get_job(j).title if store.get_job(j) else None)} for j in sorted(ids)[:30]]  # noqa: E731
    hours_span = max((until - since).total_seconds() / 3600, 1e-9)

    return {
        "window": {"since": since, "until": until, "hours": round(hours_span, 2)},
        "volume": {"jobs": len(fire), "per_hour_avg": round(len(fire) / hours_span, 1),
                   "busiest_hour": max(((h, n) for h, n in hourly), key=lambda x: x[1], default=None),
                   "most_new_in_one_poll": polls[0] if polls else 0},
        "speed_seen_after_publish": {"collector": _dist(lag(fire)), "per_search": _dist(lag(search))},
        "completeness": {"found_by_per_search": len(found_by_search), "also_seen_by_collector": len(both),
                         "missed_by_collector": missed_by_firehose[:30],
                         "collector_seen_first_by": _dist(firehose_lead),
                         "collector_first_in": sum(1 for x in firehose_lead if x > 0)},
        "granularity": {"query": query, "upwork_matched": len(upwork), "local_matched": len(local),
                        "both": len(upwork & local),
                        "upwork_only": titles(upwork - local),      # we would lose these by matching locally
                        "local_only": titles(local - upwork),       # we would add these (Upwork did not return them)
                        "collected_without_details": no_details},
    }
