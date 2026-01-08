from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import Engine, text

CORE_SCHEMA: str = "core"
PLATFORM_UPWORK: str = "upwork"

_DEFAULT_POLL_SECONDS = 60
_DEFAULT_TOP_N = 25


_WS_RE = re.compile(r"\s+")


def normalize_terms(raw: str) -> str:
    """
    Normalize user-provided terms for dedupe:
    - strip
    - collapse whitespace
    - lowercase
    """
    return _WS_RE.sub(" ", raw.strip()).lower()


def query_hash(platform: str, terms_norm: str, quote_terms: bool, poll_seconds: int, top_n: int) -> str:
    """
    Stable signature for a query definition.

    Note: we include poll_seconds and top_n here so two users with same terms
    but different polling settings become different queries (predictable behavior).
    You can refactor later to share scrapes across intervals.
    """
    s = f"{platform}|{terms_norm}|quote={int(quote_terms)}|poll={poll_seconds}|top={top_n}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SubscriptionRow:
    subscription_id: int
    chat_id: int
    query_id: int
    platform: str
    terms_raw: str
    terms_norm: str
    quote_terms: bool
    poll_seconds: int
    top_n: int
    primed_at: Optional[str]  # timestamptz string


def ensure_core_schema_and_tables(engine: Engine) -> None:
    """
    Create core schema + tables if not present.
    """
    with engine.begin() as con:
        con.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CORE_SCHEMA}";'))

        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".chats (
                    chat_id     BIGINT PRIMARY KEY,
                    chat_type   TEXT NOT NULL DEFAULT 'unknown',
                    title       TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )

        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".queries (
                    query_id      BIGSERIAL PRIMARY KEY,
                    platform      TEXT NOT NULL,
                    terms_raw     TEXT NOT NULL,
                    terms_norm    TEXT NOT NULL,
                    quote_terms   BOOLEAN NOT NULL DEFAULT FALSE,
                    poll_seconds  INT NOT NULL DEFAULT {_DEFAULT_POLL_SECONDS},
                    top_n         INT NOT NULL DEFAULT {_DEFAULT_TOP_N},
                    qhash         TEXT NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )

        con.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS queries_qhash_uq
                ON "{CORE_SCHEMA}".queries (qhash);
                """
            )
        )

        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".subscriptions (
                    subscription_id BIGSERIAL PRIMARY KEY,
                    chat_id         BIGINT NOT NULL REFERENCES "{CORE_SCHEMA}".chats(chat_id) ON DELETE CASCADE,
                    query_id        BIGINT NOT NULL REFERENCES "{CORE_SCHEMA}".queries(query_id) ON DELETE CASCADE,
                    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
                    primed_at       TIMESTAMPTZ,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (chat_id, query_id)
                );
                """
            )
        )

        con.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS subscriptions_enabled_idx
                ON "{CORE_SCHEMA}".subscriptions (enabled);
                """
            )
        )

        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".deliveries (
                    subscription_id BIGINT NOT NULL REFERENCES "{CORE_SCHEMA}".subscriptions(subscription_id) ON DELETE CASCADE,
                    job_key         TEXT NOT NULL,
                    delivered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (subscription_id, job_key)
                );
                """
            )
        )


def upsert_chat(engine: Engine, chat_id: int, chat_type: str, title: Optional[str]) -> None:
    """
    Ensure chat exists, update metadata.
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".chats (chat_id, chat_type, title)
    VALUES (:chat_id, :chat_type, :title)
    ON CONFLICT (chat_id) DO UPDATE
    SET chat_type = EXCLUDED.chat_type,
        title = EXCLUDED.title,
        updated_at = NOW();
    """
    with engine.begin() as con:
        con.execute(text(sql), {"chat_id": chat_id, "chat_type": chat_type, "title": title})


def create_or_get_query(
    engine: Engine,
    *,
    platform: str,
    terms_raw: str,
    quote_terms: bool,
    poll_seconds: int,
    top_n: int,
) -> int:
    """
    Create query if not exists (by qhash). Returns query_id.
    """
    terms_norm = normalize_terms(terms_raw)
    h = query_hash(platform, terms_norm, quote_terms, poll_seconds, top_n)

    insert_sql = f"""
    INSERT INTO "{CORE_SCHEMA}".queries
      (platform, terms_raw, terms_norm, quote_terms, poll_seconds, top_n, qhash)
    VALUES
      (:platform, :terms_raw, :terms_norm, :quote_terms, :poll_seconds, :top_n, :qhash)
    ON CONFLICT (qhash) DO NOTHING;
    """

    select_sql = f"""
    SELECT query_id
    FROM "{CORE_SCHEMA}".queries
    WHERE qhash = :qhash;
    """

    with engine.begin() as con:
        con.execute(
            text(insert_sql),
            {
                "platform": platform,
                "terms_raw": terms_raw.strip(),
                "terms_norm": terms_norm,
                "quote_terms": bool(quote_terms),
                "poll_seconds": int(poll_seconds),
                "top_n": int(top_n),
                "qhash": h,
            },
        )
        qid = con.execute(text(select_sql), {"qhash": h}).scalar_one()
        return int(qid)


def subscribe_chat_to_query(engine: Engine, chat_id: int, query_id: int) -> int:
    """
    Ensure a (chat_id, query_id) subscription exists, enabled.
    Returns subscription_id.
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".subscriptions (chat_id, query_id, enabled)
    VALUES (:chat_id, :query_id, TRUE)
    ON CONFLICT (chat_id, query_id) DO UPDATE
    SET enabled = TRUE,
        updated_at = NOW()
    RETURNING subscription_id;
    """
    with engine.begin() as con:
        sub_id = con.execute(text(sql), {"chat_id": chat_id, "query_id": query_id}).scalar_one()
        return int(sub_id)


def list_subscriptions_for_chat(engine: Engine, chat_id: int, *, platform: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT
      s.subscription_id,
      s.enabled,
      s.primed_at,
      q.query_id,
      q.platform,
      q.terms_raw,
      q.poll_seconds,
      q.top_n,
      q.quote_terms
    FROM "{CORE_SCHEMA}".subscriptions s
    JOIN "{CORE_SCHEMA}".queries q ON q.query_id = s.query_id
    WHERE s.chat_id = :chat_id
      AND q.platform = :platform
    ORDER BY s.subscription_id DESC;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"chat_id": chat_id, "platform": platform}).mappings().all()
        return [dict(r) for r in rows]


def set_subscription_enabled(engine: Engine, chat_id: int, *, enabled: bool) -> int:
    """
    Enable/disable all subscriptions for this chat. Returns affected count.
    """
    sql = f"""
    UPDATE "{CORE_SCHEMA}".subscriptions
    SET enabled = :enabled,
        updated_at = NOW()
    WHERE chat_id = :chat_id;
    """
    with engine.begin() as con:
        res = con.execute(text(sql), {"chat_id": chat_id, "enabled": bool(enabled)})
        return int(res.rowcount or 0)


def remove_subscription(engine: Engine, chat_id: int, subscription_id: int) -> bool:
    """
    Delete a subscription (only if it belongs to chat_id). Returns True if deleted.
    """
    sql = f"""
    DELETE FROM "{CORE_SCHEMA}".subscriptions
    WHERE subscription_id = :sid AND chat_id = :chat_id;
    """
    with engine.begin() as con:
        res = con.execute(text(sql), {"sid": int(subscription_id), "chat_id": int(chat_id)})
        return (res.rowcount or 0) > 0


def update_query_settings_for_chat(
    engine: Engine,
    chat_id: int,
    *,
    platform: str,
    poll_seconds: Optional[int] = None,
    top_n: Optional[int] = None,
) -> int:
    """
    Update poll/top for ALL queries used by this chat for this platform.
    Returns number of updated query rows.
    """
    sets: List[str] = []
    params: Dict[str, Any] = {"chat_id": chat_id, "platform": platform}

    if poll_seconds is not None:
        sets.append("poll_seconds = :poll_seconds")
        params["poll_seconds"] = int(poll_seconds)
    if top_n is not None:
        sets.append("top_n = :top_n")
        params["top_n"] = int(top_n)

    if not sets:
        return 0

    sql = f"""
    UPDATE "{CORE_SCHEMA}".queries q
    SET {", ".join(sets)}
    FROM "{CORE_SCHEMA}".subscriptions s
    WHERE s.query_id = q.query_id
      AND s.chat_id = :chat_id
      AND q.platform = :platform;
    """
    with engine.begin() as con:
        res = con.execute(text(sql), params)
        return int(res.rowcount or 0)


def list_enabled_subscriptions(engine: Engine, *, platform: str) -> List[SubscriptionRow]:
    """
    Get all enabled subscriptions for a given platform with query details.
    """
    sql = f"""
    SELECT
      s.subscription_id,
      s.chat_id,
      s.query_id,
      s.primed_at,
      q.platform,
      q.terms_raw,
      q.terms_norm,
      q.quote_terms,
      q.poll_seconds,
      q.top_n
    FROM "{CORE_SCHEMA}".subscriptions s
    JOIN "{CORE_SCHEMA}".queries q ON q.query_id = s.query_id
    WHERE s.enabled = TRUE
      AND q.platform = :platform;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"platform": platform}).mappings().all()
        out: List[SubscriptionRow] = []
        for r in rows:
            out.append(
                SubscriptionRow(
                    subscription_id=int(r["subscription_id"]),
                    chat_id=int(r["chat_id"]),
                    query_id=int(r["query_id"]),
                    platform=str(r["platform"]),
                    terms_raw=str(r["terms_raw"]),
                    terms_norm=str(r["terms_norm"]),
                    quote_terms=bool(r["quote_terms"]),
                    poll_seconds=int(r["poll_seconds"]),
                    top_n=int(r["top_n"]),
                    primed_at=(str(r["primed_at"]) if r["primed_at"] is not None else None),
                )
            )
        return out


def mark_subscription_primed(engine: Engine, subscription_id: int) -> None:
    sql = f"""
    UPDATE "{CORE_SCHEMA}".subscriptions
    SET primed_at = NOW(),
        updated_at = NOW()
    WHERE subscription_id = :sid;
    """
    with engine.begin() as con:
        con.execute(text(sql), {"sid": int(subscription_id)})


def try_mark_delivered(engine: Engine, subscription_id: int, job_key: str) -> bool:
    """
    Insert (subscription_id, job_key) if not present.
    Returns True if inserted (meaning: not delivered before).
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".deliveries (subscription_id, job_key)
    VALUES (:sid, :job_key)
    ON CONFLICT DO NOTHING;
    """
    with engine.begin() as con:
        res = con.execute(text(sql), {"sid": int(subscription_id), "job_key": str(job_key)})
        return (res.rowcount or 0) > 0
