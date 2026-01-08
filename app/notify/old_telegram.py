from __future__ import annotations

import asyncio
import os
import random
import re
import sys
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)


# =========================
# CONFIG
# =========================

BASE_DIR: Path = Path(__file__).resolve().parent
ENV_PATH: Path = BASE_DIR / ".env"

# Set a default test message for manual runs (you can change this)
DEFAULT_TEST_MESSAGE: str = "✅ Telegram notifier is working."


# =========================
# .env loader (no extra deps)
# =========================

_ENV_LINE_RE = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$""")


def load_dotenv(path: Path) -> None:
    """
    Minimal .env loader:
    - Supports KEY=VALUE
    - Supports quoted values "..." or '...'
    - Ignores comments (# ...) and blank lines
    - Does not overwrite existing environment variables
    """
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = _ENV_LINE_RE.match(line)
        if not m:
            continue

        key, val = m.group(1), m.group(2)

        # Strip inline comments for unquoted values: KEY=value # comment
        if val and val[0] not in ("'", '"') and " #" in val:
            val = val.split(" #", 1)[0].strip()

        # Unquote if needed
        if len(val) >= 2 and ((val[0] == val[-1]) and val[0] in ("'", '"')):
            val = val[1:-1]

        os.environ.setdefault(key, val)


def get_env_required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def parse_chat_id(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as e:
        raise RuntimeError(f"TELEGRAM_CHAT_ID must be an integer, got: {raw!r}") from e


# =========================
# Notifier
# =========================

async def send_telegram_message(text: str, *, parse_mode: Optional[str] = None) -> None:
    """
    Sends a message to TELEGRAM_CHAT_ID using TELEGRAM_BOT_TOKEN.

    Includes small retry handling for:
    - TelegramRetryAfter (rate limit)
    - network/server transient errors
    """
    token = get_env_required("TELEGRAM_BOT_TOKEN")
    chat_id = parse_chat_id(get_env_required("TELEGRAM_CHAT_ID"))

    bot = Bot(token=token)

    # Basic retry strategy
    max_attempts = 5
    base_delay = 1.0

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                return

            except TelegramRetryAfter as e:
                # Telegram told us exactly how long to wait
                wait_s = float(getattr(e, "retry_after", 3))
                await asyncio.sleep(wait_s)

            except (TelegramNetworkError, TelegramServerError) as e:
                # transient issues
                if attempt == max_attempts:
                    raise
                # exponential-ish backoff with jitter
                delay = base_delay * (2 ** (attempt - 1)) + random.random()
                await asyncio.sleep(delay)

            except (TelegramForbiddenError, TelegramBadRequest) as e:
                # permanent-ish errors: e.g. bot blocked, invalid chat_id, not started bot
                raise RuntimeError(
                    "Telegram rejected the request. Common causes:\n"
                    "- The user has NOT started the bot (open bot chat and press Start)\n"
                    "- TELEGRAM_CHAT_ID is wrong\n"
                    "- The bot is not in the group/channel or lacks permission\n"
                    f"Details: {e}"
                ) from e

            except TelegramAPIError as e:
                # other API errors; retry a couple times, then fail
                if attempt == max_attempts:
                    raise
                delay = base_delay * (2 ** (attempt - 1)) + random.random()
                await asyncio.sleep(delay)

    finally:
        await bot.session.close()


# =========================
# CLI-ish entry point (no args required)
# =========================

async def main() -> None:
    load_dotenv(ENV_PATH)

    # If you run: `python telegram_notify.py "hello"`
    # it will use that text; otherwise it uses DEFAULT_TEST_MESSAGE.
    msg = DEFAULT_TEST_MESSAGE
    if len(sys.argv) >= 2:
        msg = " ".join(sys.argv[1:]).strip() or DEFAULT_TEST_MESSAGE

    await send_telegram_message(msg)


if __name__ == "__main__":
    asyncio.run(main())
