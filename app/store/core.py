from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Engine, text

CORE_SCHEMA: str = "core"
PLATFORM_UPWORK: str = "upwork"

_DEFAULT_POLL_SECONDS = 60
_DEFAULT_TOP_N = 25

_WS_RE = re.compile(r"\s+")


# =========================
# Normalization / hashing
# =========================

def normalize_terms(raw: str) -> str:
    """
    Normalize user-provided terms for dedupe:
    - strip
    - collapse whitespace
    - lowercase
    """
    return _WS_RE.sub(" ", raw.strip()).lower()


def query_hash(
    *,
    portal_user_id: str,
    platform: str,
    compiled_query: str,
    poll_seconds: int,
    top_n: int,
) -> str:
    """
    Stable signature for a user's query definition.

    We include portal_user_id so each user's queries are treated as their own,
    even if another user has the same compiled_query/settings.
    """
    s = f"{portal_user_id}|{platform}|{compiled_query.strip()}|poll={poll_seconds}|top={top_n}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# =========================
# Row models
# =========================

@dataclass(frozen=True)
class SubscriptionRow:
    subscription_id: int
    chat_id: int
    query_id: int

    # Query fields
    platform: str
    portal_user_id: Optional[str]
    raw_text: Optional[str]
    compiled_query: Optional[str]

    # Legacy fields (still kept)
    terms_raw: Optional[str]
    terms_norm: Optional[str]
    quote_terms: bool

    poll_seconds: int
    top_n: int
    enabled: bool
    next_run_at: Optional[str]

    # Subscription fields
    primed_at: Optional[str]  # timestamptz string


# =========================
# Schema / tables
# =========================

def ensure_core_schema_and_tables(engine: Engine) -> None:
    """
    Create/upgrade core schema + tables (idempotent).

    This function supports your existing "v1" tables and upgrades them
    to support:
      - portal login mapping (users/auth_links)
      - subscription gating (portal_subscriptions)
      - richer queries (raw_text/compiled_query/portal_user_id)
      - scheduling (next_run_at)
    """
    with engine.begin() as con:
        con.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CORE_SCHEMA}";'))

        # ---- chats (delivery targets)
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

        # ---- users (telegram identity -> portal identity)
        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".users (
                    telegram_user_id BIGINT PRIMARY KEY,
                    portal_user_id   TEXT UNIQUE,
                    username         TEXT,
                    first_name       TEXT,
                    last_name        TEXT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )

        # ---- auth_links (one-time tokens to link Telegram -> portal)
        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".auth_links (
                    token            TEXT PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL REFERENCES "{CORE_SCHEMA}".users(telegram_user_id) ON DELETE CASCADE,
                    chat_id          BIGINT NOT NULL REFERENCES "{CORE_SCHEMA}".chats(chat_id) ON DELETE CASCADE,
                    expires_at       TIMESTAMPTZ NOT NULL,
                    used_at          TIMESTAMPTZ,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )
        con.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS auth_links_expires_used_idx
                ON "{CORE_SCHEMA}".auth_links (expires_at, used_at);
                """
            )
        )

        # ---- portal_subscriptions (paid status controlled by your portal)
        # NOTE: This name avoids clashing with your existing core.subscriptions (chat<->query)
        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".portal_subscriptions (
                    portal_user_id TEXT PRIMARY KEY,
                    status         TEXT NOT NULL DEFAULT 'inactive',  -- active/inactive/trial/past_due...
                    valid_until    TIMESTAMPTZ,
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )
        con.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS portal_subscriptions_status_idx
                ON "{CORE_SCHEMA}".portal_subscriptions (status);
                """
            )
        )

        # ---- queries (v1 create + v2 upgrade-in-place)
        # If table doesn't exist, create with v2-friendly structure (still includes legacy cols).
        con.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{CORE_SCHEMA}".queries (
                    query_id      BIGSERIAL PRIMARY KEY,

                    -- New (v2)
                    portal_user_id TEXT,
                    raw_text        TEXT,
                    compiled_query  TEXT,
                    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
                    next_run_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    -- Existing (v1 legacy)
                    platform      TEXT NOT NULL,
                    terms_raw     TEXT NOT NULL DEFAULT '',
                    terms_norm    TEXT NOT NULL DEFAULT '',
                    quote_terms   BOOLEAN NOT NULL DEFAULT FALSE,
                    poll_seconds  INT NOT NULL DEFAULT {_DEFAULT_POLL_SECONDS},
                    top_n         INT NOT NULL DEFAULT {_DEFAULT_TOP_N},
                    qhash         TEXT NOT NULL DEFAULT '',
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        )

        # Upgrade older deployments (safe no-op if columns already exist)
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS portal_user_id TEXT;'))
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS raw_text TEXT;'))
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS compiled_query TEXT;'))
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;'))
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW();'))
        con.execute(text(f'ALTER TABLE "{CORE_SCHEMA}".queries ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();'))

        # Existing unique index (v1): global qhash unique. Keep if it exists.
        con.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS queries_qhash_uq
                ON "{CORE_SCHEMA}".queries (qhash);
                """
            )
        )

        # New index for scheduling/due selection
        con.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS queries_due_idx
                ON "{CORE_SCHEMA}".queries (platform, enabled, next_run_at);
                """
            )
        )
        con.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS queries_portal_user_idx
                ON "{CORE_SCHEMA}".queries (portal_user_id);
                """
            )
        )

        # ---- subscriptions (chat <-> query mapping) (existing table)
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

        # ---- deliveries (dedupe per subscription) (existing table)
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


# =========================
# Chat/user helpers
# =========================

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


def upsert_user_seen(
    engine: Engine,
    telegram_user_id: int,
    *,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> None:
    """
    Ensure a Telegram user exists; update basic profile + last_seen_at.
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".users (telegram_user_id, username, first_name, last_name)
    VALUES (:telegram_user_id, :username, :first_name, :last_name)
    ON CONFLICT (telegram_user_id) DO UPDATE
    SET username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        last_seen_at = NOW();
    """
    with engine.begin() as con:
        con.execute(
            text(sql),
            {
                "telegram_user_id": int(telegram_user_id),
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
            },
        )


def get_portal_user_id(engine: Engine, telegram_user_id: int) -> Optional[str]:
    sql = f"""
    SELECT portal_user_id
    FROM "{CORE_SCHEMA}".users
    WHERE telegram_user_id = :tid;
    """
    with engine.begin() as con:
        return con.execute(text(sql), {"tid": int(telegram_user_id)}).scalar_one_or_none()


def set_portal_user_id(engine: Engine, telegram_user_id: int, portal_user_id: str) -> None:
    """
    Called by your portal after user logs in and confirms linking.
    """
    sql = f"""
    UPDATE "{CORE_SCHEMA}".users
    SET portal_user_id = :puid
    WHERE telegram_user_id = :tid;
    """
    with engine.begin() as con:
        con.execute(text(sql), {"tid": int(telegram_user_id), "puid": str(portal_user_id)})


# =========================
# Portal subscription helpers
# =========================

def upsert_portal_subscription(
    engine: Engine,
    portal_user_id: str,
    *,
    status: str,
    valid_until: Optional[datetime] = None,
) -> None:
    """
    Upsert the portal subscription status. Your portal/payment webhook should call this.
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".portal_subscriptions (portal_user_id, status, valid_until)
    VALUES (:puid, :status, :valid_until)
    ON CONFLICT (portal_user_id) DO UPDATE
    SET status = EXCLUDED.status,
        valid_until = EXCLUDED.valid_until,
        updated_at = NOW();
    """
    with engine.begin() as con:
        con.execute(
            text(sql),
            {
                "puid": str(portal_user_id),
                "status": str(status),
                "valid_until": valid_until,
            },
        )


def is_subscription_active(engine: Engine, portal_user_id: str) -> bool:
    """
    True if portal_subscriptions says 'active' AND (valid_until is null or in the future).
    """
    sql = f"""
    SELECT status, valid_until
    FROM "{CORE_SCHEMA}".portal_subscriptions
    WHERE portal_user_id = :puid;
    """
    with engine.begin() as con:
        row = con.execute(text(sql), {"puid": str(portal_user_id)}).mappings().first()
        if not row:
            return False

        status = str(row["status"] or "").lower()
        if status != "active":
            return False

        valid_until = row["valid_until"]
        if valid_until is None:
            return True

        # valid_until is a tz-aware datetime from PG driver
        now = datetime.now(timezone.utc)
        return valid_until > now


# =========================
# Auth link helpers (portal linking)
# =========================

def create_auth_link(
    engine: Engine,
    *,
    token: str,
    telegram_user_id: int,
    chat_id: int,
    expires_at: datetime,
) -> None:
    """
    Insert a one-time auth link token. The portal consumes it to link accounts.
    """
    sql = f"""
    INSERT INTO "{CORE_SCHEMA}".auth_links (token, telegram_user_id, chat_id, expires_at)
    VALUES (:token, :tid, :chat_id, :expires_at);
    """
    with engine.begin() as con:
        con.execute(
            text(sql),
            {"token": str(token), "tid": int(telegram_user_id), "chat_id": int(chat_id), "expires_at": expires_at},
        )


def consume_auth_link(engine: Engine, token: str) -> Optional[Tuple[int, int]]:
    """
    Mark token as used if valid and not expired.
    Returns (telegram_user_id, chat_id) if token is valid, else None.
    """
    sql = f"""
    UPDATE "{CORE_SCHEMA}".auth_links
    SET used_at = NOW()
    WHERE token = :token
      AND used_at IS NULL
      AND expires_at > NOW()
    RETURNING telegram_user_id, chat_id;
    """
    with engine.begin() as con:
        row = con.execute(text(sql), {"token": str(token)}).first()
        if not row:
            return None
        tid, chat_id = row
        return int(tid), int(chat_id)


# =========================
# Query + subscription helpers
# =========================

def create_or_get_query_for_user(
    engine: Engine,
    *,
    portal_user_id: str,
    platform: str,
    raw_text: str,
    compiled_query: str,
    poll_seconds: int,
    top_n: int,
    quote_terms: bool = False,
) -> int:
    """
    Create a query record (owned by portal_user_id) if not exists.
    Returns query_id.

    NOTE: We still keep legacy terms_raw/terms_norm for compatibility,
    but you should treat raw_text/compiled_query as the new source of truth.
    """
    terms_norm = normalize_terms(compiled_query)
    h = query_hash(
        portal_user_id=str(portal_user_id),
        platform=str(platform),
        compiled_query=str(compiled_query),
        poll_seconds=int(poll_seconds),
        top_n=int(top_n),
    )

    insert_sql = f"""
    INSERT INTO "{CORE_SCHEMA}".queries
      (platform, terms_raw, terms_norm, quote_terms, poll_seconds, top_n, qhash,
       portal_user_id, raw_text, compiled_query, enabled, next_run_at, updated_at)
    VALUES
      (:platform, :terms_raw, :terms_norm, :quote_terms, :poll_seconds, :top_n, :qhash,
       :portal_user_id, :raw_text, :compiled_query, TRUE, NOW(), NOW())
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
                "platform": str(platform),
                "terms_raw": compiled_query.strip(),       # legacy mirror
                "terms_norm": terms_norm,                 # legacy mirror
                "quote_terms": bool(quote_terms),
                "poll_seconds": int(poll_seconds),
                "top_n": int(top_n),
                "qhash": h,
                "portal_user_id": str(portal_user_id),
                "raw_text": raw_text.strip(),
                "compiled_query": compiled_query.strip(),
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
        sub_id = con.execute(text(sql), {"chat_id": int(chat_id), "query_id": int(query_id)}).scalar_one()
        return int(sub_id)


def list_subscriptions_for_chat(engine: Engine, chat_id: int, *, platform: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT
      s.subscription_id,
      s.enabled AS sub_enabled,
      s.primed_at,
      q.query_id,
      q.platform,
      q.portal_user_id,
      q.raw_text,
      q.compiled_query,
      q.poll_seconds,
      q.top_n,
      q.quote_terms,
      q.enabled AS query_enabled,
      q.next_run_at
    FROM "{CORE_SCHEMA}".subscriptions s
    JOIN "{CORE_SCHEMA}".queries q ON q.query_id = s.query_id
    WHERE s.chat_id = :chat_id
      AND q.platform = :platform
    ORDER BY s.subscription_id DESC;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"chat_id": int(chat_id), "platform": str(platform)}).mappings().all()
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
        res = con.execute(text(sql), {"chat_id": int(chat_id), "enabled": bool(enabled)})
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
    params: Dict[str, Any] = {"chat_id": int(chat_id), "platform": str(platform)}

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
    SET {", ".join(sets)},
        updated_at = NOW()
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

    NOTE: A subscription is considered runnable only if:
      - subscriptions.enabled = TRUE
      - queries.enabled = TRUE
    (subscription gating by portal subscription is handled elsewhere.)
    """
    sql = f"""
    SELECT
      s.subscription_id,
      s.chat_id,
      s.query_id,
      s.enabled AS sub_enabled,
      s.primed_at,
      q.platform,
      q.portal_user_id,
      q.raw_text,
      q.compiled_query,
      q.terms_raw,
      q.terms_norm,
      q.quote_terms,
      q.poll_seconds,
      q.top_n,
      q.enabled AS query_enabled,
      q.next_run_at
    FROM "{CORE_SCHEMA}".subscriptions s
    JOIN "{CORE_SCHEMA}".queries q ON q.query_id = s.query_id
    WHERE s.enabled = TRUE
      AND q.enabled = TRUE
      AND q.platform = :platform;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"platform": str(platform)}).mappings().all()
        out: List[SubscriptionRow] = []
        for r in rows:
            out.append(
                SubscriptionRow(
                    subscription_id=int(r["subscription_id"]),
                    chat_id=int(r["chat_id"]),
                    query_id=int(r["query_id"]),
                    platform=str(r["platform"]),
                    portal_user_id=(str(r["portal_user_id"]) if r["portal_user_id"] is not None else None),
                    raw_text=(str(r["raw_text"]) if r["raw_text"] is not None else None),
                    compiled_query=(str(r["compiled_query"]) if r["compiled_query"] is not None else None),
                    terms_raw=(str(r["terms_raw"]) if r["terms_raw"] is not None else None),
                    terms_norm=(str(r["terms_norm"]) if r["terms_norm"] is not None else None),
                    quote_terms=bool(r["quote_terms"]),
                    poll_seconds=int(r["poll_seconds"]),
                    top_n=int(r["top_n"]),
                    enabled=bool(r["sub_enabled"]) and bool(r["query_enabled"]),
                    next_run_at=(str(r["next_run_at"]) if r["next_run_at"] is not None else None),
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


# =========================
# Scheduling helpers (Postgres is source of truth)
# =========================

def list_due_queries(engine: Engine, *, platform: str, limit: int = 100) -> List[int]:
    """
    Return query_ids that are due right now (enabled + next_run_at <= now).
    Used by the scheduler (Redis Streams producer).
    """
    sql = f"""
    SELECT query_id
    FROM "{CORE_SCHEMA}".queries
    WHERE platform = :platform
      AND enabled = TRUE
      AND next_run_at <= NOW()
    ORDER BY next_run_at ASC
    LIMIT :limit;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"platform": str(platform), "limit": int(limit)}).all()
        return [int(r[0]) for r in rows]


def bump_next_run(engine: Engine, *, query_id: int) -> None:
    """
    Set next_run_at = NOW() + poll_seconds for this query.
    """
    sql = f"""
    UPDATE "{CORE_SCHEMA}".queries
    SET next_run_at = NOW() + (poll_seconds || ' seconds')::interval,
        updated_at = NOW()
    WHERE query_id = :qid;
    """
    with engine.begin() as con:
        con.execute(text(sql), {"qid": int(query_id)})
