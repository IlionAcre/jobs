from __future__ import annotations

import asyncio
import html
import queue
import re
import random
import threading
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)


def strip_html(text: str) -> str:
    """Telegram HTML -> plain text (keeps a link's address so it is still usable)."""
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', lambda m: f"{m.group(2)} {html.unescape(m.group(1))}", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class TelegramNotifier:
    """
    Background-thread Telegram sender (aiogram Bot) for synchronous workers.

    Why: SeleniumBase + long-running loops are sync; this avoids asyncio loop conflicts.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._q: "queue.Queue[Optional[Tuple[int, str, bool]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def send(self, chat_id: int, text: str, html: bool = False) -> None:
        """`html=True`: Telegram HTML formatting. If Telegram rejects the markup, the text is sent again
        with the tags removed, so a formatting mistake never loses an alert."""
        self._q.put((int(chat_id), text, html))

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run())
        loop.close()

    async def _run(self) -> None:
        bot = Bot(token=self._token)
        try:
            while True:
                item = await asyncio.to_thread(self._q.get)
                if item is None:
                    return
                chat_id, text, html = item
                await self._send_with_retries(bot, chat_id, text, html)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass

    async def _send_with_retries(self, bot: Bot, chat_id: int, text: str, html: bool = False) -> None:
        max_attempts = 5
        base_delay = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True,
                                       parse_mode="HTML" if html else None)
                return

            except TelegramBadRequest as e:
                if html and "parse" in str(e).lower():
                    html, text = False, strip_html(text)  # broken markup: send it plain rather than not at all
                    continue
                print(f"[tg] rejected chat_id={chat_id}: {e}")
                return

            except TelegramRetryAfter as e:
                wait_s = float(getattr(e, "retry_after", 3))
                await asyncio.sleep(wait_s)

            except (TelegramNetworkError, TelegramServerError):
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.random())

            except (TelegramForbiddenError, TelegramBadRequest) as e:
                # user blocked bot, invalid chat_id, etc.
                print(f"[tg] rejected chat_id={chat_id}: {e}")
                return

            except TelegramAPIError:
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.random())
