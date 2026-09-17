# monitor_uw_cm.py
from __future__ import annotations

import asyncio
import os
import queue
import random
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from dotenv import load_dotenv

# MODIFIED IMPORTS for Camufoux migration
from app.ingest.upwork_cm import build_or_query, build_search_url
from app.parse.upwork import parse_jobs
# MODIFIED IMPORT for browser_cm (Playwright/Camoufox)
from app.shared.browser_cm import FetchOptions, camoufox_session
from app.shared.captcha_handle_cm import solve_captcha
from app.config import load_config
from app.store.jobs import ensure_schema, make_engine, upsert_job

# =========================
# CONFIG (edit these)
# =========================
WATCH_TERMS: List[str] = ["python", "scraping"]
POLL_SECONDS: int = 60
TOP_N: int = 25
CHALLENGE_TIMEOUT_S: int = 180

# These are NOT in .env because you want to change them per service/script run
DB_SCHEMA: str = "upwork"
DB_SOURCE: str = "upwork"

CONFIG_PATH: Path = Path(__file__).resolve().parent / "app" / "config" / "config.yaml"
# =========================


def _build_db_dsn_from_env() -> str:
    """
    Build SQLAlchemy DSN from discrete .env variables:
      DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, (optional) DB_DRIVER
    """
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ["DB_NAME"]
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    driver = os.environ.get("DB_DRIVER", "psycopg")  # or pg8000

    return f"postgresql+{driver}://{user}:{password}@{host}:{port}/{name}"


def _wait_until_ready(page, *, job_tile_css: str, timeout_s: int) -> None:
    start = time.time()
    last_url: Optional[str] = None

    while True:
        cur_url = page.url
        if cur_url != last_url:
            print(f"[monitor] current url: {cur_url}")
            last_url = cur_url

        try:
            # Playwright wait_for_selector, timeout in ms
            page.wait_for_selector(job_tile_css, timeout=2000)
            return
        except Exception:
            pass

        if time.time() - start > timeout_s:
            raise TimeoutError(
                f"Timed out waiting for results after {timeout_s}s. Last URL: {cur_url}"
            )

        # Attempt to solve captcha if present
        solve_captcha(page)

        time.sleep(2.0)


def _apply_waits(page, opts: FetchOptions) -> None:
    if opts.wait_after_open_s and opts.wait_after_open_s > 0:
        time.sleep(opts.wait_after_open_s)
    if opts.wait_for_css:
        try:
            page.wait_for_selector(opts.wait_for_css, timeout=opts.wait_timeout_s * 1000)
        except Exception:
            pass


def _print_new_jobs(jobs: List[object]) -> None:
    if not jobs:
        print("[NEW] none")
        return

    print(f"[NEW] {len(jobs)} new job(s):")
    for j in jobs:
        title = getattr(j, "title", None) or "(no title)"
        url = getattr(j, "url", None) or ""
        budget = getattr(j, "budget", None) or ""
        duration = getattr(j, "duration", None) or ""
        location = getattr(j, "location", None) or ""
        job_type = getattr(j, "job_type", None) or ""
        posted = getattr(j, "posted", None) or ""
        tags = getattr(j, "tags", None) or []

        meta_bits = [b for b in [job_type, location, budget, duration, posted] if b]
        meta = " | ".join(meta_bits)

        print(f"- {title}" + (f" | {meta}" if meta else ""))
        if url:
            print(f"  {url}")
        if tags:
            try:
                print(f"  tags: {', '.join(tags)}")
            except Exception:
                print(f"  tags: {tags!r}")


# =========================
# TELEGRAM (aiogram) NOTIFIER
# =========================

def _get_telegram_config_from_env() -> Optional[Tuple[str, int]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id_raw:
        return None
    try:
        chat_id = int(chat_id_raw)
    except ValueError as e:
        raise RuntimeError(f"TELEGRAM_CHAT_ID must be an integer, got: {chat_id_raw!r}") from e
    return token, chat_id


def _format_job_lines(j: object, include_description: bool = True) -> List[str]:
    title = getattr(j, "title", None) or "(no title)"
    url = getattr(j, "url", None) or ""
    budget = getattr(j, "budget", None) or ""
    duration = getattr(j, "duration", None) or ""
    location = getattr(j, "location", None) or ""
    job_type = getattr(j, "job_type", None) or ""
    posted = getattr(j, "posted", None) or ""
    tags = getattr(j, "tags", None) or []
    snippet = getattr(j, "snippet", None) or ""

    meta_bits = [b for b in [job_type, location, budget, duration, posted] if b]
    meta = " | ".join(meta_bits)

    lines = [f"• {title}" + (f" — {meta}" if meta else "")]
    if include_description and snippet:
        # Truncate if very long? Telegram limit is per-message, but let's keep it sane
        lines.append(f"  {snippet[:300]}..." if len(snippet) > 300 else f"  {snippet}")

    if url:
        lines.append(url)
    if tags:
        try:
            lines.append(f"tags: {', '.join(tags)}")
        except Exception:
            lines.append(f"tags: {tags!r}")
    return lines


def _chunk_text(lines: List[str], *, max_chars: int = 3500) -> List[str]:
    # Telegram hard limit is 4096 chars; stay safely under it.
    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    for line in lines:
        add_len = len(line) + 1  # newline
        if buf and (buf_len + add_len > max_chars):
            chunks.append("\n".join(buf).strip())
            buf = []
            buf_len = 0
        buf.append(line)
        buf_len += add_len

    if buf:
        chunks.append("\n".join(buf).strip())

    return chunks


class TelegramNotifier:
    """
    Synchronous-friendly Telegram notifier:
    - Runs aiogram Bot in a dedicated background thread with its own asyncio loop.
    - Your main code calls .send(text) from synchronous code safely.
    """

    def __init__(self, token: str, chat_id: int) -> None:
        self._token = token
        self._chat_id = chat_id

        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def send(self, text: str) -> None:
        # Non-blocking enqueue. If you want backpressure, use put(..., timeout=...)
        self._q.put(text)

    def close(self) -> None:
        self._q.put(None)  # sentinel
        self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run(loop))
        loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        bot = Bot(token=self._token)

        try:
            while True:
                # Wait for next message in a thread-safe queue
                text = await asyncio.to_thread(self._q.get)
                if text is None:
                    return

                # Send with retries
                await self._send_with_retries(bot, text)

        finally:
            try:
                await bot.session.close()
            except Exception:
                pass

    async def _send_with_retries(self, bot: Bot, text: str) -> None:
        max_attempts = 5
        base_delay = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                await bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
                return

            except TelegramRetryAfter as e:
                wait_s = float(getattr(e, "retry_after", 3))
                await asyncio.sleep(wait_s)

            except (TelegramNetworkError, TelegramServerError):
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.random())

            except (TelegramForbiddenError, TelegramBadRequest) as e:
                # Permanent-ish errors
                raise RuntimeError(
                    "Telegram rejected the request. Common causes:\n"
                    "- In a DM: you must open the bot chat and press Start\n"
                    "- In a group/channel: bot must be added (and admin for channels)\n"
                    "- TELEGRAM_CHAT_ID is wrong\n"
                    f"Details: {e}"
                ) from e

            except TelegramAPIError:
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.random())


def _notify_new_jobs(notifier: TelegramNotifier, jobs: List[object], show_description: bool = True) -> None:
    if not jobs:
        return

    header = [f"🔔 {len(jobs)} new Upwork job(s)"]
    body_lines: List[str] = []
    for j in jobs:
        body_lines.extend(_format_job_lines(j, include_description=show_description))
        body_lines.append("")

    chunks = _chunk_text(header + [""] + body_lines, max_chars=3500)
    for msg in chunks:
        if msg.strip():
            notifier.send(msg)


# =========================
# MAIN
# =========================

def main() -> None:
    print(f"[monitor_cm] running file: {Path(__file__).resolve()}")

    # Load .env once for DB + Telegram
    load_dotenv()

    cfg = load_config(CONFIG_PATH)

    query = build_or_query(WATCH_TERMS, quote_terms=cfg.upwork.quote_terms)
    url = build_search_url(cfg.upwork.base_search_url, query)

    tc = cfg.camufoux if (cfg.camufoux and cfg.camufoux.active) else None

    # Helper to pick value: camufoux override > default config
    def _pick(override, default):
        return override if override is not None else default

    # Determine values
    w_open = _pick(tc.wait_after_open_s if tc else None, cfg.waits.wait_after_open_s)
    w_css_on = _pick(tc.wait_for_job_tiles if tc else None, cfg.waits.wait_for_job_tiles)
    w_css_sel = _pick(tc.job_tile_css if tc else None, cfg.waits.job_tile_css)
    w_timeout = _pick(tc.wait_timeout_s if tc else None, cfg.waits.wait_timeout_s)
    
    w_pause_val = _pick(tc.pause_for_manual_solve if tc else None, cfg.waits.pause_for_manual_solve)
    w_post_enter = _pick(tc.post_enter_wait_s if tc else None, cfg.waits.post_enter_wait_s)

    opts = FetchOptions(
        wait_after_open_s=w_open,
        wait_for_css=(w_css_sel if w_css_on else None),
        wait_timeout_s=w_timeout,
        pause_for_manual_solve=w_pause_val, 
        post_enter_wait_s=w_post_enter,
    )

    dsn = _build_db_dsn_from_env()
    engine = make_engine(dsn)
    ensure_schema(engine, schema=DB_SCHEMA)

    tg_cfg = _get_telegram_config_from_env()
    notifier: Optional[TelegramNotifier] = None
    if tg_cfg:
        tg_token, tg_chat_id = tg_cfg
        notifier = TelegramNotifier(token=tg_token, chat_id=tg_chat_id)
        print("[monitor] telegram notifications: ENABLED")
    else:
        print("[monitor] telegram notifications: DISABLED (missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")

    print(f"[monitor] url:   {url}")
    print(f"[monitor] polling every {POLL_SECONDS}s")
    print(f"[monitor] writing to: {DB_SCHEMA}.jobs (source={DB_SOURCE})")

    camufoux_cfg = getattr(cfg, "camufoux", None)

    try:
        # Use camoufox_session (Playwright)
        with camoufox_session(cfg.selenium, camufoux_cfg=camufoux_cfg) as page:
            print(f"[monitor] navigating to {url}...")
            try:
                # Use domcontentloaded to avoid waiting for slow tracking scripts
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                print("[monitor] navigation complete. Waiting for results/captcha...")
            except Exception as e:
                 print(f"[monitor] navigation timed out or failed: {e}. Continuing to wait loop...")

            job_tile_css = cfg.waits.job_tile_css
            _wait_until_ready(page, job_tile_css=job_tile_css, timeout_s=CHALLENGE_TIMEOUT_S)

            # Prime DB with current results (so we don't print old jobs as "new")
            _apply_waits(page, opts)
            html_text = page.content()
            jobs = parse_jobs(html_text)[:TOP_N]
            for j in jobs:
                upsert_job(engine, schema=DB_SCHEMA, source=DB_SOURCE, job=j)

            print("[monitor] primed DB with current top results. Now monitoring for new jobs...\n")

            while True:
                try:
                    time.sleep(POLL_SECONDS)

                    # Refresh using goto with commit wait (fastest)
                    try:
                        page.goto(url, wait_until="commit", timeout=30000)
                    except Exception as e:
                        print(f"[monitor] navigation/reload error: {e}. Continuing to see if page is usable...")

                    _wait_until_ready(
                        page,
                        job_tile_css=job_tile_css,
                        timeout_s=int(cfg.waits.wait_timeout_s),
                    )
                    _apply_waits(page, opts)

                    html_text = page.content()
                    jobs = parse_jobs(html_text)[:TOP_N]

                    new_jobs: List[object] = []
                    for j in jobs:
                        inserted_new, _ = upsert_job(engine, schema=DB_SCHEMA, source=DB_SOURCE, job=j)
                        if inserted_new:
                            new_jobs.append(j)

                    _print_new_jobs(new_jobs)
                    print()

                    if notifier is not None and new_jobs:
                        _notify_new_jobs(
                            notifier,
                            new_jobs,
                            show_description=cfg.upwork.show_description
                        )

                except KeyboardInterrupt:
                    print("\n[monitor] stopped.")
                    return
                except Exception as e:
                    print(f"[monitor] error: {e!r} (retrying in {POLL_SECONDS}s)")

    finally:
        if notifier is not None:
            try:
                notifier.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
