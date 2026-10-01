"""
Postgres storage for the scraper pipeline (schema `scraper`).

  searches       one row per distinct search text; polled once however many chats subscribe
  subscriptions  chat <-> search, with optional per-subscription word filters
  jobs           one row per Upwork job id, full detail + raw JSON
  search_hits    which search surfaced which job, and when first (this is the "is it new?" memory)
  deliveries     which subscription was already told about which job (never alert twice)

`schema_ddl()` is the single definition of the tables; the Alembic migration and the tests both use it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Engine, text

from app.scraper.models import PLATFORM_UPWORK, Job, JobRef

DEFAULT_SCHEMA = "scraper"
_WS = re.compile(r"\s+")


def schema_ddl(schema: str = DEFAULT_SCHEMA) -> List[str]:
    s = f'"{schema}"'
    return [
        f"CREATE SCHEMA IF NOT EXISTS {s}",
        f"""CREATE TABLE IF NOT EXISTS {s}.searches (
            search_id            BIGSERIAL PRIMARY KEY,
            platform             TEXT NOT NULL,
            query_text           TEXT NOT NULL,
            query_norm           TEXT NOT NULL,
            poll_seconds         INT,
            enabled              BOOLEAN NOT NULL DEFAULT TRUE,
            next_run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            primed_at            TIMESTAMPTZ,
            last_ok_at           TIMESTAMPTZ,
            last_error           TEXT,
            last_error_at        TIMESTAMPTZ,
            consecutive_failures INT NOT NULL DEFAULT 0,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (platform, query_norm)
        )""",
        f"CREATE INDEX IF NOT EXISTS searches_due_idx ON {s}.searches (enabled, next_run_at)",
        f"""CREATE TABLE IF NOT EXISTS {s}.subscriptions (
            subscription_id BIGSERIAL PRIMARY KEY,
            chat_id         BIGINT NOT NULL,
            search_id       BIGINT NOT NULL REFERENCES {s}.searches(search_id) ON DELETE CASCADE,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            include_words   TEXT[] NOT NULL DEFAULT '{{}}',
            exclude_words   TEXT[] NOT NULL DEFAULT '{{}}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (chat_id, search_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS subscriptions_search_idx ON {s}.subscriptions (search_id) WHERE enabled",
        f"""CREATE TABLE IF NOT EXISTS {s}.jobs (
            platform      TEXT NOT NULL,
            job_id        TEXT NOT NULL,
            url           TEXT NOT NULL,
            title         TEXT,
            description   TEXT,
            skills        TEXT[] NOT NULL DEFAULT '{{}}',
            job_type      TEXT,
            fixed_amount  NUMERIC,
            hourly_min    NUMERIC,
            hourly_max    NUMERIC,
            tier          TEXT,
            duration      TEXT,
            workload      TEXT,
            create_time   TIMESTAMPTZ,
            publish_time  TIMESTAMPTZ,
            raw           JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (platform, job_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS jobs_first_seen_idx ON {s}.jobs (first_seen_at)",
        f"""CREATE TABLE IF NOT EXISTS {s}.search_hits (
            search_id     BIGINT NOT NULL REFERENCES {s}.searches(search_id) ON DELETE CASCADE,
            job_id        TEXT NOT NULL,
            publish_time  TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (search_id, job_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS search_hits_first_seen_idx ON {s}.search_hits (first_seen_at)",
        f"""CREATE TABLE IF NOT EXISTS {s}.deliveries (
            subscription_id BIGINT NOT NULL REFERENCES {s}.subscriptions(subscription_id) ON DELETE CASCADE,
            job_id          TEXT NOT NULL,
            status          TEXT NOT NULL,
            delivered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (subscription_id, job_id)
        )""",
        f"CREATE INDEX IF NOT EXISTS deliveries_delivered_idx ON {s}.deliveries (delivered_at)",
    ]


def schema_drop(schema: str = DEFAULT_SCHEMA) -> List[str]:
    return [f'DROP SCHEMA IF EXISTS "{schema}" CASCADE']


def normalize_query(query_text: str) -> str:
    """Two searches are the same search if they differ only in case or spacing."""
    return _WS.sub(" ", query_text.strip()).lower()


@dataclass(frozen=True, slots=True)
class SearchRow:
    search_id: int
    platform: str
    query_text: str
    poll_seconds: Optional[int]
    enabled: bool
    primed: bool


@dataclass(frozen=True, slots=True)
class SubscriptionRow:
    subscription_id: int
    chat_id: int
    search_id: int
    include_words: Tuple[str, ...]
    exclude_words: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChatSubscription:
    """A subscription as its owner sees it."""

    subscription_id: int
    search_id: int
    query_text: str
    enabled: bool
    include_words: Tuple[str, ...]
    exclude_words: Tuple[str, ...]


class ScraperStore:
    def __init__(self, engine: Engine, schema: str = DEFAULT_SCHEMA) -> None:
        self._engine = engine
        self._s = f'"{schema}"'
        self._schema = schema

    def create_tables(self) -> None:
        with self._engine.begin() as con:
            for stmt in schema_ddl(self._schema):
                con.execute(text(stmt))

    # --- searches and subscriptions -----------------------------------------------------------

    def upsert_search(self, query_text: str, *, platform: str = PLATFORM_UPWORK, poll_seconds: Optional[int] = None) -> int:
        sql = f"""
            INSERT INTO {self._s}.searches (platform, query_text, query_norm, poll_seconds)
            VALUES (:platform, :text, :norm, :poll)
            ON CONFLICT (platform, query_norm) DO UPDATE SET enabled = TRUE,
                poll_seconds = COALESCE(EXCLUDED.poll_seconds, {self._s}.searches.poll_seconds)
            RETURNING search_id"""
        with self._engine.begin() as con:
            return int(con.execute(text(sql), {"platform": platform, "text": query_text.strip(),
                                               "norm": normalize_query(query_text), "poll": poll_seconds}).scalar_one())

    def subscribe(self, chat_id: int, search_id: int, *, include_words: Sequence[str] = (),
                  exclude_words: Sequence[str] = ()) -> int:
        sql = f"""
            INSERT INTO {self._s}.subscriptions (chat_id, search_id, include_words, exclude_words)
            VALUES (:chat, :search, :inc, :exc)
            ON CONFLICT (chat_id, search_id) DO UPDATE SET enabled = TRUE,
                include_words = EXCLUDED.include_words, exclude_words = EXCLUDED.exclude_words
            RETURNING subscription_id"""
        with self._engine.begin() as con:
            return int(con.execute(text(sql), {"chat": int(chat_id), "search": int(search_id),
                                               "inc": list(include_words), "exc": list(exclude_words)}).scalar_one())

    def get_search(self, search_id: int) -> Optional[SearchRow]:
        sql = f"""SELECT search_id, platform, query_text, poll_seconds, enabled, primed_at IS NOT NULL
                  FROM {self._s}.searches WHERE search_id = :id"""
        with self._engine.begin() as con:
            row = con.execute(text(sql), {"id": int(search_id)}).first()
        return SearchRow(int(row[0]), row[1], row[2], row[3], bool(row[4]), bool(row[5])) if row else None

    def count_enabled_searches(self) -> int:
        with self._engine.begin() as con:
            return int(con.execute(text(f"SELECT count(*) FROM {self._s}.searches WHERE enabled")).scalar_one())

    def due_searches(self, limit: int = 200) -> List[SearchRow]:
        """Enabled searches whose time has come and that someone is subscribed to."""
        sql = f"""
            SELECT s.search_id, s.platform, s.query_text, s.poll_seconds, s.enabled, s.primed_at IS NOT NULL
            FROM {self._s}.searches s
            WHERE s.enabled AND s.next_run_at <= NOW()
              AND EXISTS (SELECT 1 FROM {self._s}.subscriptions b WHERE b.search_id = s.search_id AND b.enabled)
            ORDER BY s.next_run_at
            LIMIT :limit"""
        with self._engine.begin() as con:
            rows = con.execute(text(sql), {"limit": int(limit)}).all()
        return [SearchRow(int(r[0]), r[1], r[2], r[3], bool(r[4]), bool(r[5])) for r in rows]

    def schedule_next(self, search_id: int, seconds: float) -> None:
        sql = f"UPDATE {self._s}.searches SET next_run_at = NOW() + make_interval(secs => :secs) WHERE search_id = :id"
        with self._engine.begin() as con:
            con.execute(text(sql), {"secs": float(seconds), "id": int(search_id)})

    def mark_primed(self, search_id: int) -> None:
        with self._engine.begin() as con:
            con.execute(text(f"UPDATE {self._s}.searches SET primed_at = COALESCE(primed_at, NOW()) WHERE search_id = :id"),
                        {"id": int(search_id)})

    def record_poll_ok(self, search_id: int) -> None:
        sql = f"UPDATE {self._s}.searches SET last_ok_at = NOW(), consecutive_failures = 0 WHERE search_id = :id"
        with self._engine.begin() as con:
            con.execute(text(sql), {"id": int(search_id)})

    def record_poll_error(self, search_id: int, error: str) -> None:
        sql = f"""UPDATE {self._s}.searches SET last_error = :err, last_error_at = NOW(),
                  consecutive_failures = consecutive_failures + 1 WHERE search_id = :id"""
        with self._engine.begin() as con:
            con.execute(text(sql), {"err": error[:500], "id": int(search_id)})

    def subscriptions_for_search(self, search_id: int) -> List[SubscriptionRow]:
        sql = f"""SELECT subscription_id, chat_id, search_id, include_words, exclude_words
                  FROM {self._s}.subscriptions WHERE search_id = :id AND enabled ORDER BY subscription_id"""
        with self._engine.begin() as con:
            rows = con.execute(text(sql), {"id": int(search_id)}).all()
        return [SubscriptionRow(int(r[0]), int(r[1]), int(r[2]), tuple(r[3] or ()), tuple(r[4] or ())) for r in rows]

    # --- a chat's own subscriptions (bot / CLI) ------------------------------------------------
    # Every method takes the chat id, so one chat can never read or change another chat's subscriptions.

    def subscriptions_for_chat(self, chat_id: int) -> List[ChatSubscription]:
        sql = f"""SELECT b.subscription_id, b.search_id, s.query_text, b.enabled, b.include_words, b.exclude_words
                  FROM {self._s}.subscriptions b JOIN {self._s}.searches s ON s.search_id = b.search_id
                  WHERE b.chat_id = :chat ORDER BY b.subscription_id"""
        with self._engine.begin() as con:
            rows = con.execute(text(sql), {"chat": int(chat_id)}).all()
        return [ChatSubscription(int(r[0]), int(r[1]), r[2], bool(r[3]), tuple(r[4] or ()), tuple(r[5] or ())) for r in rows]

    def set_filters(self, chat_id: int, subscription_id: int, *, include_words: Sequence[str],
                    exclude_words: Sequence[str]) -> bool:
        sql = f"""UPDATE {self._s}.subscriptions SET include_words = :inc, exclude_words = :exc
                  WHERE subscription_id = :sub AND chat_id = :chat"""
        with self._engine.begin() as con:
            res = con.execute(text(sql), {"inc": list(include_words), "exc": list(exclude_words),
                                          "sub": int(subscription_id), "chat": int(chat_id)})
            return (res.rowcount or 0) > 0

    def remove_subscription(self, chat_id: int, subscription_id: int) -> bool:
        sql = f"DELETE FROM {self._s}.subscriptions WHERE subscription_id = :sub AND chat_id = :chat"
        with self._engine.begin() as con:
            return (con.execute(text(sql), {"sub": int(subscription_id), "chat": int(chat_id)}).rowcount or 0) > 0

    def set_chat_enabled(self, chat_id: int, enabled: bool) -> int:
        """Pause or resume all of a chat's subscriptions. A search nobody is subscribed to stops being polled."""
        sql = f"UPDATE {self._s}.subscriptions SET enabled = :on WHERE chat_id = :chat AND enabled <> :on"
        with self._engine.begin() as con:
            return con.execute(text(sql), {"on": bool(enabled), "chat": int(chat_id)}).rowcount or 0

    # --- recent activity (dashboard) ----------------------------------------------------------

    def recent_jobs(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Newest jobs with how long after publishing we first saw them (priming batches excluded)."""
        sql = f"""
            SELECT h.first_seen_at, h.publish_time, j.title, j.url, s.query_text,
                   EXTRACT(EPOCH FROM (h.first_seen_at - h.publish_time)) AS lag_s
            FROM {self._s}.search_hits h
            JOIN {self._s}.searches s ON s.search_id = h.search_id
            LEFT JOIN {self._s}.jobs j ON j.job_id = h.job_id
            WHERE s.primed_at IS NOT NULL AND h.first_seen_at > s.primed_at + INTERVAL '5 seconds'
            ORDER BY h.first_seen_at DESC LIMIT :limit"""
        with self._engine.begin() as con:
            rows = con.execute(text(sql), {"limit": int(limit)}).all()
        return [{"seen_at": r[0], "publish_time": r[1], "title": r[2], "url": r[3], "query": r[4],
                 "lag_s": float(r[5]) if r[5] is not None else None} for r in rows]

    def activity(self, hours: float = 24.0) -> Dict[str, Any]:
        since = "NOW() - make_interval(secs => :secs)"
        params = {"secs": float(hours) * 3600}
        with self._engine.begin() as con:
            lag = con.execute(text(f"""
                SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY x), percentile_cont(0.9) WITHIN GROUP (ORDER BY x)
                FROM (SELECT EXTRACT(EPOCH FROM (h.first_seen_at - h.publish_time)) AS x
                      FROM {self._s}.search_hits h JOIN {self._s}.searches s ON s.search_id = h.search_id
                      WHERE h.first_seen_at > {since} AND h.publish_time IS NOT NULL
                        AND s.primed_at IS NOT NULL AND h.first_seen_at > s.primed_at + INTERVAL '5 seconds'
                        AND h.first_seen_at - h.publish_time < INTERVAL '30 minutes') t"""), params).one()
            deliveries = con.execute(text(
                f"SELECT status, count(*) FROM {self._s}.deliveries WHERE delivered_at > {since} GROUP BY 1"), params).all()
        return {"hours": hours, "new_jobs": int(lag[0]),
                "lag_median_s": float(lag[1]) if lag[1] is not None else None,
                "lag_p90_s": float(lag[2]) if lag[2] is not None else None,
                "deliveries": {r[0]: int(r[1]) for r in deliveries}}

    # --- jobs ---------------------------------------------------------------------------------

    def record_hits(self, search_id: int, refs: Sequence[JobRef]) -> List[str]:
        """Remember these jobs for this search; return the ids that were not known before, in input order."""
        if not refs:
            return []
        sql = f"""INSERT INTO {self._s}.search_hits (search_id, job_id, publish_time)
                  VALUES (:search, :job, :pub) ON CONFLICT DO NOTHING RETURNING job_id"""
        new = set()
        with self._engine.begin() as con:
            for ref in refs:
                if con.execute(text(sql), {"search": int(search_id), "job": ref.job_id, "pub": ref.publish_time}).first():
                    new.add(ref.job_id)
        return [r.job_id for r in refs if r.job_id in new]

    def upsert_jobs(self, jobs: Sequence[Job]) -> None:
        sql = f"""
            INSERT INTO {self._s}.jobs (platform, job_id, url, title, description, skills, job_type, fixed_amount,
                                        hourly_min, hourly_max, tier, duration, workload, create_time, publish_time, raw)
            VALUES (:platform, :job_id, :url, :title, :description, :skills, :job_type, :fixed_amount,
                    :hourly_min, :hourly_max, :tier, :duration, :workload, :create_time, :publish_time, CAST(:raw AS JSONB))
            ON CONFLICT (platform, job_id) DO UPDATE SET
                title = EXCLUDED.title, description = EXCLUDED.description, skills = EXCLUDED.skills,
                job_type = EXCLUDED.job_type, fixed_amount = EXCLUDED.fixed_amount, hourly_min = EXCLUDED.hourly_min,
                hourly_max = EXCLUDED.hourly_max, tier = EXCLUDED.tier, duration = EXCLUDED.duration,
                workload = EXCLUDED.workload, raw = EXCLUDED.raw, last_seen_at = NOW()"""
        with self._engine.begin() as con:
            for j in jobs:
                con.execute(text(sql), {
                    "platform": j.platform, "job_id": j.job_id, "url": j.url, "title": j.title,
                    "description": j.description, "skills": list(j.skills), "job_type": j.job_type,
                    "fixed_amount": j.fixed_amount, "hourly_min": j.hourly_min, "hourly_max": j.hourly_max,
                    "tier": j.tier, "duration": j.duration, "workload": j.workload,
                    "create_time": j.create_time, "publish_time": j.publish_time, "raw": json.dumps(j.raw),
                })

    def get_job(self, job_id: str, *, platform: str = PLATFORM_UPWORK) -> Optional[Job]:
        sql = f"""SELECT job_id, url, title, description, skills, job_type, fixed_amount, hourly_min, hourly_max,
                         tier, duration, workload, create_time, publish_time, platform
                  FROM {self._s}.jobs WHERE platform = :platform AND job_id = :job"""
        with self._engine.begin() as con:
            r = con.execute(text(sql), {"platform": platform, "job": job_id}).first()
        if not r:
            return None
        num = lambda v: float(v) if v is not None else None  # noqa: E731
        return Job(job_id=r[0], url=r[1], title=r[2], description=r[3], skills=tuple(r[4] or ()), job_type=r[5],
                   fixed_amount=num(r[6]), hourly_min=num(r[7]), hourly_max=num(r[8]), tier=r[9], duration=r[10],
                   workload=r[11], create_time=r[12], publish_time=r[13], platform=r[14])

    # --- deliveries ---------------------------------------------------------------------------

    def try_mark_delivered(self, subscription_id: int, job_id: str, status: str) -> bool:
        """True the first time this subscription meets this job; False if it was already handled."""
        sql = f"""INSERT INTO {self._s}.deliveries (subscription_id, job_id, status)
                  VALUES (:sub, :job, :status) ON CONFLICT DO NOTHING"""
        with self._engine.begin() as con:
            return (con.execute(text(sql), {"sub": int(subscription_id), "job": job_id, "status": status}).rowcount or 0) > 0

    def chat_already_has_job(self, chat_id: int, job_id: str) -> bool:
        """True if any subscription of this chat was already sent (or shadow-sent) this job."""
        sql = f"""SELECT 1 FROM {self._s}.deliveries d
                  JOIN {self._s}.subscriptions b ON b.subscription_id = d.subscription_id
                  WHERE b.chat_id = :chat AND d.job_id = :job AND d.status IN ('sent', 'shadow') LIMIT 1"""
        with self._engine.begin() as con:
            return con.execute(text(sql), {"chat": int(chat_id), "job": job_id}).first() is not None

    # --- housekeeping -------------------------------------------------------------------------

    def cleanup(self, retention_days: int) -> Dict[str, int]:
        """Drop rows older than the retention window. Deliveries and hits go together with their jobs,
        so a job that is still in the results can't be re-alerted after its records were purged."""
        cutoff = "NOW() - make_interval(days => :days)"
        counts: Dict[str, int] = {}
        with self._engine.begin() as con:
            for table, column in (("deliveries", "delivered_at"), ("search_hits", "first_seen_at"), ("jobs", "last_seen_at")):
                res = con.execute(text(f"DELETE FROM {self._s}.{table} WHERE {column} < {cutoff}"), {"days": int(retention_days)})
                counts[table] = res.rowcount or 0
        return counts

    def snapshot(self) -> Dict[str, Any]:
        """Numbers for `scraper status`."""
        with self._engine.begin() as con:
            searches = con.execute(text(f"""
                SELECT s.search_id, s.query_text, s.enabled, s.primed_at IS NOT NULL, s.last_ok_at, s.consecutive_failures,
                       s.last_error, s.next_run_at, s.created_at,
                       (SELECT count(*) FROM {self._s}.subscriptions b WHERE b.search_id = s.search_id AND b.enabled)
                FROM {self._s}.searches s ORDER BY s.search_id""")).all()
            one = lambda q: con.execute(text(q)).scalar_one()  # noqa: E731
            return {
                "searches": [{"search_id": int(r[0]), "query": r[1], "enabled": bool(r[2]), "primed": bool(r[3]),
                              "last_ok_at": r[4], "consecutive_failures": int(r[5]), "last_error": r[6],
                              "next_run_at": r[7], "created_at": r[8], "subscribers": int(r[9])} for r in searches],
                "jobs_total": one(f"SELECT count(*) FROM {self._s}.jobs"),
                "jobs_last_hour": one(f"SELECT count(*) FROM {self._s}.jobs WHERE first_seen_at > NOW() - INTERVAL '1 hour'"),
                "deliveries_last_hour": {r[0]: int(r[1]) for r in con.execute(text(
                    f"SELECT status, count(*) FROM {self._s}.deliveries WHERE delivered_at > NOW() - INTERVAL '1 hour' GROUP BY 1")).all()},
            }

