"""
Comparison with the legacy monitor: did the new pipeline see AND alert on the same new jobs, and when?

Legacy `monitor_uw.py` writes to upwork.jobs (job id is inside the URL); the pipeline writes to
scraper.search_hits and scraper.deliveries. Jobs are matched on the Upwork id. Read-only.

A job counts as alerted when a delivery row says `sent` (live) or `shadow` (would have been sent).
`filtered` and `duplicate` are deliberate non-alerts and are reported separately.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List

from sqlalchemy import Engine, text

_SQL = """
WITH legacy AS (
    SELECT substring(url from '(~[0-9a-zA-Z]+)') AS job_id, min(first_seen_at) AS seen, min(title) AS title
    FROM upwork.jobs
    WHERE first_seen_at >= :since AND first_seen_at < NOW() - make_interval(secs => :grace) AND source = 'upwork'
    GROUP BY 1
),
deliv AS (
    SELECT job_id, array_agg(DISTINCT status) AS statuses FROM scraper.deliveries GROUP BY 1
),
new AS (
    SELECT h.job_id, min(h.first_seen_at) AS seen, min(h.publish_time) AS published
    FROM scraper.search_hits h JOIN scraper.searches s ON s.search_id = h.search_id
    WHERE h.first_seen_at >= :since AND lower(s.query_norm) = lower(:query_norm)
      AND h.first_seen_at > s.primed_at + interval '5 seconds'      -- not the priming batch
    GROUP BY 1
)
SELECT COALESCE(l.job_id, n.job_id) AS job_id, l.seen AS legacy_seen, n.seen AS new_seen, n.published, l.title,
       COALESCE(d.statuses, ARRAY[]::text[]) AS statuses
FROM legacy l FULL OUTER JOIN new n ON n.job_id = l.job_id
LEFT JOIN deliv d ON d.job_id = COALESCE(l.job_id, n.job_id)
WHERE COALESCE(l.job_id, n.job_id) IS NOT NULL
ORDER BY COALESCE(n.seen, l.seen)
"""


_ALERTED = {"sent", "shadow"}
_GRACE_S = 180  # a job the legacy monitor saw in the last few minutes may still be on its way here


def shadow_report(engine: Engine, *, query_norm: str, hours: float) -> Dict[str, Any]:
    with engine.begin() as con:
        since = con.execute(text("SELECT GREATEST(NOW() - make_interval(secs => :s), "
                                 "(SELECT min(primed_at) FROM scraper.searches WHERE query_norm = :q))"),
                            {"s": hours * 3600, "q": query_norm}).scalar_one()
        rows = con.execute(text(_SQL), {"since": since, "query_norm": query_norm, "grace": _GRACE_S}).mappings().all()

    both = [r for r in rows if r["legacy_seen"] and r["new_seen"]]
    only_legacy = [r for r in rows if r["legacy_seen"] and not r["new_seen"]]
    only_new = [r for r in rows if r["new_seen"] and not r["legacy_seen"]]
    alerted = [r for r in both if _ALERTED & set(r["statuses"])]
    held_back = [r for r in both if not (_ALERTED & set(r["statuses"])) and r["statuses"]]  # filtered / duplicate
    not_alerted = [r for r in both if not r["statuses"]]  # seen but no delivery row at all: a lost alert
    lead = sorted((r["legacy_seen"] - r["new_seen"]).total_seconds() for r in both)  # > 0: new pipeline was first
    lag = sorted((r["new_seen"] - r["published"]).total_seconds() for r in rows if r["new_seen"] and r["published"])

    def dist(xs: List[float]) -> Dict[str, Any]:
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "median_s": round(statistics.median(xs), 1), "p10_s": round(xs[len(xs) // 10], 1),
                "p90_s": round(xs[int(0.9 * (len(xs) - 1))], 1), "min_s": round(xs[0], 1), "max_s": round(xs[-1], 1)}

    return {
        "since": since, "query": query_norm,
        "seen_by_both": len(both), "only_legacy": len(only_legacy), "only_new": len(only_new),
        "alerted": len(alerted), "held_back_by_filter": len(held_back), "seen_but_not_alerted": len(not_alerted),
        "missed": len(only_legacy) + len(not_alerted),  # what the go-live gate looks at: must be 0
        "not_alerted_jobs": [{"job_id": r["job_id"], "new_seen": r["new_seen"], "title": r["title"]} for r in not_alerted[:25]],
        "new_pipeline_lead_over_legacy": dist(lead),
        "new_pipeline_first_in": sum(1 for x in lead if x > 0),
        "new_pipeline_seen_after_publish": dist(lag),
        "only_legacy_jobs": [{"job_id": r["job_id"], "legacy_seen": r["legacy_seen"], "title": r["title"]} for r in only_legacy[:25]],
        "only_new_jobs": [{"job_id": r["job_id"], "new_seen": r["new_seen"]} for r in only_new[:25]],
    }
