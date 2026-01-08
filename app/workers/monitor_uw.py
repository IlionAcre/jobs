from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional

from dotenv import load_dotenv

from app.ingest.upwork import build_or_query, build_search_url
from app.notify.telegram import TelegramNotifier
from app.parse.upwork import parse_jobs
from app.shared.browser import FetchOptions, sb_session
from app.shared.models import load_config
from app.store.core import (
    PLATFORM_UPWORK,
    SubscriptionRow,
    ensure_core_schema_and_tables,
    list_enabled_subscriptions,
    mark_subscription_primed,
    try_mark_delivered,
)
from app.store.db import build_db_dsn_from_env, make_pg_engine
from app.store.jobs import ensure_schema, upsert_job  # keep your existing global jobs storage


# =========================
# CONFIG
# =========================
CONFIG_PATH: Path = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
UPWORK_SCHEMA: str = "upwork"
UPWORK_SOURCE: str = "upwork"

TICK_SECONDS: int = 5
CHALLENGE_TIMEOUT_S: int = 180


def _job_key(j: object) -> str:
    """
    Stable key for dedupe/delivery.
    Prefer an explicit id; fallback to URL.
    """
    for attr in ("job_id", "id", "uid", "url"):
        v = getattr(j, attr, None)
        if v:
            return str(v)
    return repr(j)


def _format_job_lines(j: object) -> List[str]:
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
    return lines


def _chunk_text(lines: List[str], max_chars: int = 3500) -> List[str]:
    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for line in lines:
        add_len = len(line) + 1
        if buf and (buf_len + add_len > max_chars):
            chunks.append("\n".join(buf).strip())
            buf = []
            buf_len = 0
        buf.append(line)
        buf_len += add_len
    if buf:
        chunks.append("\n".join(buf).strip())
    return chunks


def _terms_to_list(raw: str) -> List[str]:
    """
    Convert stored terms text into term list for build_or_query.
    Simple behavior: split by spaces; you can upgrade later (quotes, commas, etc).
    """
    return [t for t in raw.strip().split() if t]


def main() -> None:
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    cfg = load_config(CONFIG_PATH)

    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)

    # keep your existing upwork jobs schema/table
    ensure_schema(engine, schema=UPWORK_SCHEMA)

    notifier = TelegramNotifier(token=token)

    # Schedule per query_id
    last_query_run: Dict[int, float] = {}

    opts = FetchOptions(
        wait_after_open_s=cfg.waits.wait_after_open_s,
        wait_for_css=(cfg.waits.job_tile_css if cfg.waits.wait_for_job_tiles else None),
        wait_timeout_s=cfg.waits.wait_timeout_s,
        pause_for_manual_solve=False,
        post_enter_wait_s=cfg.waits.post_enter_wait_s,
    )

    print("[worker] running. Ctrl+C to stop.")
    try:
        with sb_session(cfg.selenium) as sb:
            while True:
                try:
                    subs = list_enabled_subscriptions(engine, platform=PLATFORM_UPWORK)
                    if not subs:
                        time.sleep(TICK_SECONDS)
                        continue

                    # group subscriptions by query_id (scrape each query once)
                    by_query: DefaultDict[int, List[SubscriptionRow]] = defaultdict(list)
                    for s in subs:
                        by_query[s.query_id].append(s)

                    now = time.time()

                    for query_id, group in by_query.items():
                        # all rows share the same query settings by design
                        poll_seconds = group[0].poll_seconds
                        top_n = group[0].top_n
                        quote_terms = group[0].quote_terms
                        terms_raw = group[0].terms_raw

                        due = (now - last_query_run.get(query_id, 0.0)) >= float(poll_seconds)
                        if not due:
                            continue

                        terms = _terms_to_list(terms_raw)
                        query = build_or_query(terms, quote_terms=quote_terms)
                        url = build_search_url(cfg.upwork.base_search_url, query)

                        # Navigate & fetch
                        sb.open(url)

                        if cfg.waits.wait_for_job_tiles:
                            sb.wait_for_element_present(cfg.waits.job_tile_css, timeout=CHALLENGE_TIMEOUT_S)

                        # Apply waits (same as your monitor)
                        if opts.wait_after_open_s and opts.wait_after_open_s > 0:
                            sb.sleep(opts.wait_after_open_s)
                        if opts.wait_for_css:
                            sb.wait_for_element_present(opts.wait_for_css, timeout=opts.wait_timeout_s)

                        html_text = sb.get_page_source()
                        jobs = parse_jobs(html_text)[:top_n]

                        # upsert into global jobs table (keeps your existing store useful)
                        for j in jobs:
                            upsert_job(engine, schema=UPWORK_SCHEMA, source=UPWORK_SOURCE, job=j)

                        # For each subscription: prime once, then deliver new items
                        for s in group:
                            if s.primed_at is None:
                                # Prime: mark current jobs as delivered without notifying
                                for j in jobs:
                                    try_mark_delivered(engine, s.subscription_id, _job_key(j))
                                mark_subscription_primed(engine, s.subscription_id)
                                print(f"[worker] primed sub_id={s.subscription_id} (chat_id={s.chat_id})")
                                continue

                            new_jobs: List[object] = []
                            for j in jobs:
                                if try_mark_delivered(engine, s.subscription_id, _job_key(j)):
                                    new_jobs.append(j)

                            if new_jobs:
                                header = [f"🔔 {len(new_jobs)} new job(s)", f"terms: {terms_raw}", ""]
                                body: List[str] = []
                                for j in new_jobs:
                                    body.extend(_format_job_lines(j))
                                    body.append("")
                                for msg in _chunk_text(header + body):
                                    if msg.strip():
                                        notifier.send(s.chat_id, msg)

                                print(f"[worker] notified sub_id={s.subscription_id} new={len(new_jobs)}")
                            else:
                                print(f"[worker] sub_id={s.subscription_id} new=0")

                        last_query_run[query_id] = now

                    time.sleep(TICK_SECONDS)

                except KeyboardInterrupt:
                    print("\n[worker] stopped.")
                    return
                except Exception as e:
                    print(f"[worker] error: {e!r} (retrying in {TICK_SECONDS}s)")
                    time.sleep(TICK_SECONDS)

    finally:
        notifier.close()


if __name__ == "__main__":
    main()
