from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import Engine, text

from app.config import load_config
from app.ingest.upwork import build_search_url
from app.notify.telegram import TelegramNotifier
from app.parse.upwork import parse_jobs
from app.queue.redis_streams import (
    ensure_consumer_group,
    get_redis_client,
    xack,
    xadd_work,
    xautoclaim,
    xreadgroup,
)
from app.settings import load_worker_settings
from app.shared.browser import FetchOptions, sb_session
from app.shared.health import check_db, check_redis, require_env
from app.shared.logging_utils import configure_logging, get_logger
from app.store.core import CORE_SCHEMA, PLATFORM_UPWORK, ensure_core_schema_and_tables, mark_subscription_primed, try_mark_delivered
from app.store.db import build_db_dsn_from_env, make_engine
from app.store.jobs import ensure_schema, upsert_job


CONFIG_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
CHALLENGE_TIMEOUT_S = 180


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
        from datetime import datetime, timezone
        return valid_until > datetime.now(timezone.utc)
    except Exception:
        return True


def _attempt_from_fields(fields: Dict[str, str]) -> int:
    try:
        return int(fields.get("attempt", "0"))
    except Exception:
        return 0


def main() -> None:
    load_dotenv()
    configure_logging()
    log = get_logger("worker")

    require_env(["DB_NAME", "DB_USER", "DB_PASSWORD", "TELEGRAM_BOT_TOKEN"])
    settings = load_worker_settings()

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    cfg = load_config(CONFIG_PATH)

    engine = make_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)
    ensure_schema(engine, schema="upwork")
    check_db(engine)

    r = get_redis_client()
    check_redis(r)
    ensure_consumer_group(r, stream=settings.stream, group=settings.group)

    notifier = TelegramNotifier(token=tg_token)

    opts = FetchOptions(
        wait_after_open_s=cfg.waits.wait_after_open_s,
        wait_for_css=(cfg.waits.job_tile_css if cfg.waits.wait_for_job_tiles else None),
        wait_timeout_s=cfg.waits.wait_timeout_s,
        pause_for_manual_solve=False,
        post_enter_wait_s=cfg.waits.post_enter_wait_s,
    )

    log.info(
        "worker_ready",
        extra={
            "stream": settings.stream,
            "group": settings.group,
            "consumer": settings.consumer,
            "dlq_stream": settings.dlq_stream,
            "max_attempts": settings.max_attempts,
        },
    )

    autoclaim_start = "0-0"

    try:
        with sb_session(cfg.selenium) as sb:
            while True:
                try:
                    try:
                        autoclaim_start, reclaimed = xautoclaim(
                            r,
                            stream=settings.stream,
                            group=settings.group,
                            consumer=settings.consumer,
                            min_idle_ms=settings.autoclaim_idle_ms,
                            count=settings.read_count,
                            start_id=autoclaim_start,
                        )
                        msgs = [(mid, fields) for (mid, fields) in reclaimed] if reclaimed else []
                    except Exception:
                        msgs = []

                    if not msgs:
                        items = xreadgroup(
                            r,
                            stream=settings.stream,
                            group=settings.group,
                            consumer=settings.consumer,
                            count=settings.read_count,
                            block_ms=settings.block_ms,
                        )
                        msgs = []
                        for _stream, entries in items:
                            for mid, fields in entries:
                                msgs.append((mid, fields))

                    if not msgs:
                        continue

                    for msg_id, fields in msgs:
                        if not isinstance(fields, dict):
                            xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                            continue

                        query_id: Optional[int] = None
                        attempt = _attempt_from_fields(fields)

                        try:
                            qid_s = fields.get("query_id")
                            if not qid_s:
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            query_id = int(qid_s)
                            q = _load_query(engine, query_id)
                            if not q:
                                log.info("worker_query_missing", extra={"query_id": query_id, "msg_id": msg_id})
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            if str(q["platform"]) != PLATFORM_UPWORK or not bool(q["enabled"]):
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            if not _is_active(str(q["status"]), q["valid_until"]):
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            compiled_query = str(q["compiled_query"] or "").strip()
                            if not compiled_query:
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            top_n = int(q["top_n"])
                            url = build_search_url(cfg.upwork.base_search_url, compiled_query)

                            sb.open(url)
                            if cfg.waits.wait_for_job_tiles:
                                sb.wait_for_element_present(cfg.waits.job_tile_css, timeout=CHALLENGE_TIMEOUT_S)
                            if opts.wait_after_open_s and opts.wait_after_open_s > 0:
                                sb.sleep(opts.wait_after_open_s)
                            if opts.wait_for_css:
                                sb.wait_for_element_present(opts.wait_for_css, timeout=opts.wait_timeout_s)

                            html_text = sb.get_page_source()
                            jobs = parse_jobs(html_text)[:top_n]

                            for j in jobs:
                                upsert_job(engine, schema="upwork", source="upwork", job=j)

                            targets = _subscription_targets_for_query(engine, query_id)
                            if not targets:
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                continue

                            for sub_id, chat_id, primed_at in targets:
                                if primed_at is None:
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

                            xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                            log.info(
                                "worker_msg_processed",
                                extra={
                                    "msg_id": msg_id,
                                    "query_id": query_id,
                                    "attempt": attempt,
                                    "targets": len(targets),
                                    "jobs": len(jobs),
                                },
                            )

                        except Exception as e:
                            next_attempt = attempt + 1
                            error_text = repr(e)
                            safe_query_id = query_id if query_id is not None else 0

                            if safe_query_id == 0 or next_attempt >= settings.max_attempts:
                                xadd_work(
                                    r,
                                    stream=settings.dlq_stream,
                                    fields={
                                        "query_id": str(safe_query_id),
                                        "source_msg_id": str(msg_id),
                                        "attempt": str(next_attempt),
                                        "error": error_text[:500],
                                        "consumer": settings.consumer,
                                    },
                                )
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                log.error(
                                    "worker_msg_dlq",
                                    extra={
                                        "msg_id": msg_id,
                                        "query_id": safe_query_id,
                                        "attempt": next_attempt,
                                        "max_attempts": settings.max_attempts,
                                        "dlq_stream": settings.dlq_stream,
                                        "error": error_text,
                                    },
                                )
                            else:
                                xadd_work(
                                    r,
                                    stream=settings.stream,
                                    fields={
                                        "query_id": str(safe_query_id),
                                        "attempt": str(next_attempt),
                                    },
                                )
                                xack(r, stream=settings.stream, group=settings.group, msg_id=msg_id)
                                log.warning(
                                    "worker_msg_requeued",
                                    extra={
                                        "msg_id": msg_id,
                                        "query_id": safe_query_id,
                                        "attempt": next_attempt,
                                        "max_attempts": settings.max_attempts,
                                        "error": error_text,
                                    },
                                )

                except KeyboardInterrupt:
                    log.info("worker_stopped")
                    return

                except Exception:
                    log.exception("worker_loop_error")
                    time.sleep(2)

    finally:
        notifier.close()
        try:
            r.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
