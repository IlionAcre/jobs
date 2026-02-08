from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import Engine, text

from app.ingest.upwork import build_search_url
from app.notify.telegram import TelegramNotifier
from app.parse.upwork import parse_jobs
from app.queue.redis_streams import (
    ensure_consumer_group,
    get_redis_client,
    xack,
    xautoclaim,
    xreadgroup,
)
from app.shared.browser import FetchOptions, sb_session
from app.shared.models import load_config
from app.store.core import CORE_SCHEMA, PLATFORM_UPWORK, ensure_core_schema_and_tables, mark_subscription_primed, try_mark_delivered
from app.store.db import build_db_dsn_from_env, make_engine
from app.store.jobs import ensure_schema, upsert_job


# -------- Redis stream config
STREAM = os.environ.get("UPWORK_STREAM", "work:upwork")
GROUP = os.environ.get("UPWORK_GROUP", "workers:upwork")
CONSUMER = os.environ.get("UPWORK_CONSUMER", "worker-1")

# -------- Behavior
CONFIG_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
CHALLENGE_TIMEOUT_S = 180

AUTOCLAIM_IDLE_MS = 60_000  # reclaim pending after 60s idle
READ_COUNT = 10
BLOCK_MS = 5000


def _job_key(j: object) -> str:
    for attr in ("job_id", "id", "uid", "url"):
        v = getattr(j, attr, None)
        if v:
            return str(v)
    return repr(j)


def _format_job(j: object) -> str:
    title = getattr(j, "title", None) or "(no title)"
    url = getattr(j, "url", None) or ""
    budget = getattr(j, "budget", None) or ""
    duration = getattr(j, "duration", None) or ""
    location = getattr(j, "location", None) or ""
    job_type = getattr(j, "job_type", None) or ""
    tags = getattr(j, "tags", None) or []

    meta_bits = [b for b in [job_type, location, budget, duration] if b]
    meta = " | ".join(meta_bits)

    lines = [f"• {title}" + (f" — {meta}" if meta else "")]
    if url:
        lines.append(url)
    if tags:
        try:
            lines.append(f"tags: {', '.join(tags)}")
        except Exception:
            lines.append(f"tags: {tags!r}")
    return "\n".join(lines)


def _chunk_text(text: str, max_chars: int = 3500) -> List[str]:
    out: List[str] = []
    s = text.strip()
    while len(s) > max_chars:
        cut = s.rfind("\n", 0, max_chars)
        if cut < 500:
            cut = max_chars
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return out


def _load_query(engine: Engine, query_id: int) -> Optional[Dict[str, object]]:
    sql = f"""
    SELECT q.query_id, q.portal_user_id, q.platform, q.raw_text, q.compiled_query,
           q.poll_seconds, q.top_n, q.enabled,
           ps.status, ps.valid_until
    FROM "{CORE_SCHEMA}".queries q
    JOIN "{CORE_SCHEMA}".portal_subscriptions ps
      ON ps.portal_user_id = q.portal_user_id
    WHERE q.query_id = :qid;
    """
    with engine.begin() as con:
        row = con.execute(text(sql), {"qid": int(query_id)}).mappings().first()
        return dict(row) if row else None


def _subscription_targets_for_query(engine: Engine, query_id: int) -> List[Tuple[int, int, Optional[str]]]:
    """
    Returns list of (subscription_id, chat_id, primed_at) for enabled subscriptions.
    """
    sql = f"""
    SELECT subscription_id, chat_id, primed_at
    FROM "{CORE_SCHEMA}".subscriptions
    WHERE query_id = :qid
      AND enabled = TRUE;
    """
    with engine.begin() as con:
        rows = con.execute(text(sql), {"qid": int(query_id)}).all()
        return [(int(r[0]), int(r[1]), (str(r[2]) if r[2] is not None else None)) for r in rows]


def _is_active(status: str, valid_until) -> bool:
    if (status or "").lower() != "active":
        return False
    if valid_until is None:
        return True
    try:
        # valid_until is datetime
        from datetime import datetime, timezone
        return valid_until > datetime.now(timezone.utc)
    except Exception:
        return True


def main() -> None:
    load_dotenv()

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not tg_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    cfg = load_config(CONFIG_PATH)

    engine = make_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)
    ensure_schema(engine, schema="upwork")

    r = get_redis_client()
    ensure_consumer_group(r, stream=STREAM, group=GROUP)

    notifier = TelegramNotifier(token=tg_token)

    opts = FetchOptions(
        wait_after_open_s=cfg.waits.wait_after_open_s,
        wait_for_css=(cfg.waits.job_tile_css if cfg.waits.wait_for_job_tiles else None),
        wait_timeout_s=cfg.waits.wait_timeout_s,
        pause_for_manual_solve=False,
        post_enter_wait_s=cfg.waits.post_enter_wait_s,
    )

    print(f"[worker] stream={STREAM} group={GROUP} consumer={CONSUMER}")

    # For autoclaim scanning
    autoclaim_start = "0-0"

    try:
        with sb_session(cfg.selenium) as sb:
            while True:
                try:
                    # 1) Reclaim stale pending (optional but good long-term)
                    try:
                        autoclaim_start, reclaimed = xautoclaim(
                            r,
                            stream=STREAM,
                            group=GROUP,
                            consumer=CONSUMER,
                            min_idle_ms=AUTOCLAIM_IDLE_MS,
                            count=READ_COUNT,
                            start_id=autoclaim_start,
                        )
                        if reclaimed:
                            msgs = [(mid, fields) for (mid, fields) in reclaimed]
                        else:
                            msgs = []
                    except Exception:
                        msgs = []

                    # 2) Read new messages
                    if not msgs:
                        items = xreadgroup(
                            r,
                            stream=STREAM,
                            group=GROUP,
                            consumer=CONSUMER,
                            count=READ_COUNT,
                            block_ms=BLOCK_MS,
                        )
                        msgs = []
                        for _stream, entries in items:
                            for mid, fields in entries:
                                msgs.append((mid, fields))

                    if not msgs:
                        continue

                    for msg_id, fields in msgs:
                        try:
                            qid_s = fields.get("query_id") if isinstance(fields, dict) else None
                            if not qid_s:
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            query_id = int(qid_s)
                            q = _load_query(engine, query_id)
                            if not q:
                                print(f"[worker] query_id={query_id} not found; ack")
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            if str(q["platform"]) != PLATFORM_UPWORK:
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            if not bool(q["enabled"]):
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            if not _is_active(str(q["status"]), q["valid_until"]):
                                # subscription inactive; ack so it doesn't clog pending
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            compiled_query = str(q["compiled_query"] or "").strip()
                            if not compiled_query:
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            top_n = int(q["top_n"])
                            url = build_search_url(cfg.upwork.base_search_url, compiled_query)

                            # Fetch
                            sb.open(url)

                            if cfg.waits.wait_for_job_tiles:
                                sb.wait_for_element_present(cfg.waits.job_tile_css, timeout=CHALLENGE_TIMEOUT_S)

                            if opts.wait_after_open_s and opts.wait_after_open_s > 0:
                                sb.sleep(opts.wait_after_open_s)
                            if opts.wait_for_css:
                                sb.wait_for_element_present(opts.wait_for_css, timeout=opts.wait_timeout_s)

                            html_text = sb.get_page_source()
                            jobs = parse_jobs(html_text)[:top_n]

                            # Upsert jobs into your existing upwork.jobs
                            for j in jobs:
                                upsert_job(engine, schema="upwork", source="upwork", job=j)

                            targets = _subscription_targets_for_query(engine, query_id)
                            if not targets:
                                xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)
                                continue

                            # Deliver per subscription
                            for sub_id, chat_id, primed_at in targets:
                                if primed_at is None:
                                    # prime silently
                                    for j in jobs:
                                        try_mark_delivered(engine, sub_id, _job_key(j))
                                    mark_subscription_primed(engine, sub_id)
                                    continue

                                new_jobs: List[object] = []
                                for j in jobs:
                                    if try_mark_delivered(engine, sub_id, _job_key(j)):
                                        new_jobs.append(j)

                                if new_jobs:
                                    header = f"🔔 {len(new_jobs)} new job(s)\nquery: {q.get('raw_text') or compiled_query}\n\n"
                                    body = "\n\n".join(_format_job(j) for j in new_jobs)
                                    for chunk in _chunk_text(header + body):
                                        notifier.send(chat_id, chunk)

                            # ACK only after successful processing
                            xack(r, stream=STREAM, group=GROUP, msg_id=msg_id)

                        except Exception as e:
                            # Do NOT ack. It stays pending and can be reclaimed.
                            print(f"[worker] msg_id={msg_id} error: {e!r}")
                            continue

                except KeyboardInterrupt:
                    print("\n[worker] stopped.")
                    return

                except Exception as e:
                    print(f"[worker] loop error: {e!r}")
                    time.sleep(2)

    finally:
        notifier.close()
        try:
            r.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
