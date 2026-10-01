"""
Telegram bot for managing subscriptions to the scraper pipeline.

  /search <words>      watch a search in this chat          /searches   list this chat's searches
  /filter <id> ...     set include/exclude words            /remove <id>
  /pause  /resume      stop / restart all alerts here       /status   /help

`BotCore.handle(chat_id, text) -> reply` holds all the behaviour and knows nothing about Telegram, so it is
tested directly. `run_bot` is the thin aiogram adapter.

Who may use it is decided by an AccessPolicy. There is no portal or payment system yet, so the policies are
"allowlist" and "open"; a policy backed by paid subscriptions can be added here without touching the rest.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Iterable, List, Optional, Protocol, Tuple

from app.scraper.config import ScraperConfig
from app.scraper.store import ChatSubscription, ScraperStore

log = logging.getLogger("scraper.bot")

MAX_QUERY_CHARS = 200
MAX_FILTER_WORDS = 20

HELP = (
    "I watch Upwork searches and message you when a new job appears.\n\n"
    "/search python OR scraping — start watching a search\n"
    "/searches — your searches and their filters\n"
    "/filter 3 include: python, scraping; exclude: wordpress — only alert when one of the include words "
    "appears, never when an exclude word does\n"
    "/filter 3 clear — remove the filter\n"
    "/remove 3 — stop watching search 3\n"
    "/pause, /resume — stop or restart all alerts in this chat\n"
    "/status — is it working"
)


class AccessPolicy(Protocol):
    def allowed(self, chat_id: int) -> bool: ...


class OpenPolicy:
    def allowed(self, chat_id: int) -> bool:
        return True


class AllowlistPolicy:
    def __init__(self, chat_ids: Iterable[int]) -> None:
        self._chat_ids = {int(c) for c in chat_ids}

    def allowed(self, chat_id: int) -> bool:
        return int(chat_id) in self._chat_ids


def build_policy(config: ScraperConfig, owner_chat_id: Optional[int]) -> AccessPolicy:
    if config.bot.access == "open":
        return OpenPolicy()
    return AllowlistPolicy([*config.bot.allowed_chat_ids, *([owner_chat_id] if owner_chat_id is not None else [])])


def compile_query(raw: str) -> str:
    """Upwork treats a space as AND, so "python AND scraping" becomes "python scraping"; OR is kept."""
    out: List[str] = []
    for token in raw.split():
        if token.upper() == "AND":
            continue
        out.append("OR" if token.upper() == "OR" else token)
    while out and out[0] == "OR":
        out.pop(0)
    while out and out[-1] == "OR":
        out.pop()
    return " ".join(out)


def parse_filter(spec: str) -> Optional[Tuple[List[str], List[str]]]:
    """"include: a, b c; exclude: d" -> (["a", "b c"], ["d"]). None if the text isn't in that form."""
    include: List[str] = []
    exclude: List[str] = []
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        m = re.fullmatch(r"(include|exclude)\s*:\s*(.*)", part, re.I | re.S)
        if not m:
            return None
        words = [w.strip().lower() for w in m.group(2).split(",") if w.strip()]
        (include if m.group(1).lower() == "include" else exclude).extend(words)
    return (include, exclude) if (include or exclude) else None


def _describe(sub: ChatSubscription) -> str:
    line = f"#{sub.subscription_id}  {sub.query_text}" + ("" if sub.enabled else "  (paused)")
    if sub.include_words:
        line += "\n     only if it mentions: " + ", ".join(sub.include_words)
    if sub.exclude_words:
        line += "\n     never if it mentions: " + ", ".join(sub.exclude_words)
    return line


class BotCore:
    def __init__(self, store: ScraperStore, config: ScraperConfig, policy: AccessPolicy) -> None:
        self._store = store
        self._cfg = config
        self._policy = policy

    def handle(self, chat_id: int, text: str) -> Optional[str]:
        """Reply to one message, or None to stay silent (not a command)."""
        text = (text or "").strip()
        if not text.startswith("/"):
            return None
        head, _, arg = text.partition(" ")
        command = head[1:].split("@", 1)[0].lower()  # "/search@MyBot" -> "search"
        arg = arg.strip()

        if not self._policy.allowed(chat_id):
            return ("This bot is private for now. Ask the owner to allow this chat.\n"
                    f"Chat id: {chat_id}")

        handler = {
            "start": self._help, "help": self._help,
            "search": self._search, "query": self._search,
            "searches": self._searches, "queries": self._searches,
            "filter": self._filter, "remove": self._remove,
            "pause": self._pause, "resume": self._resume, "status": self._status,
        }.get(command)
        if handler is None:
            return "I don't know that command.\n\n" + HELP
        try:
            return handler(chat_id, arg)
        except Exception:  # noqa: BLE001
            log.exception("bot_command_failed", extra={"command": command})
            return "Something went wrong on my side. Please try again in a minute."

    # --- commands -------------------------------------------------------------------------------

    def _shadow_note(self) -> str:
        return ("\n\nNote: alerts are switched off for everyone right now (the service is in test mode)."
                if self._cfg.dispatcher.mode != "live" else "")

    def _help(self, chat_id: int, arg: str) -> str:
        return HELP + self._shadow_note()

    def _search(self, chat_id: int, arg: str) -> str:
        query = compile_query(arg)
        if not query:
            return "Tell me what to search for, e.g.\n/search python OR scraping"
        if len(query) > MAX_QUERY_CHARS:
            return f"That search is too long (max {MAX_QUERY_CHARS} characters)."
        mine = self._store.subscriptions_for_chat(chat_id)
        existing = next((s for s in mine if s.query_text.lower() == query.lower()), None)
        if existing is None and len(mine) >= self._cfg.bot.max_searches_per_chat:
            return (f"This chat already watches {len(mine)} searches (the limit is "
                    f"{self._cfg.bot.max_searches_per_chat}). Remove one with /remove <id> first.")
        search_id = self._store.upsert_search(query)
        sub_id = self._store.subscribe(chat_id, search_id,
                                       include_words=existing.include_words if existing else (),
                                       exclude_words=existing.exclude_words if existing else ())
        verb = "Already watching" if existing else "Now watching"
        return (f"{verb} #{sub_id}: {query}\n"
                "You'll get a message for each new job from now on (not for jobs already posted).\n"
                f"Too many loose matches? /filter {sub_id} include: word1, word2" + self._shadow_note())

    def _searches(self, chat_id: int, arg: str) -> str:
        mine = self._store.subscriptions_for_chat(chat_id)
        if not mine:
            return "No searches yet. Start one with\n/search python OR scraping"
        return "Your searches:\n" + "\n".join(_describe(s) for s in mine) + self._shadow_note()

    def _find(self, chat_id: int, token: str) -> Optional[ChatSubscription]:
        try:
            sub_id = int(token.lstrip("#"))
        except ValueError:
            return None
        return next((s for s in self._store.subscriptions_for_chat(chat_id) if s.subscription_id == sub_id), None)

    def _filter(self, chat_id: int, arg: str) -> str:
        token, _, spec = arg.partition(" ")
        sub = self._find(chat_id, token) if token else None
        if sub is None:
            return "Which search? Use the number from /searches, e.g.\n/filter 3 include: python, scraping; exclude: wordpress"
        spec = spec.strip()
        if not spec:
            return _describe(sub) + ("" if (sub.include_words or sub.exclude_words) else "\n     no filter: every match is sent")
        if spec.lower() in ("clear", "off", "none"):
            self._store.set_filters(chat_id, sub.subscription_id, include_words=[], exclude_words=[])
            return f"Filter removed from #{sub.subscription_id}. Every match will be sent."
        parsed = parse_filter(spec)
        if parsed is None:
            return "I couldn't read that. Example:\n/filter 3 include: python, scraping; exclude: wordpress\nor /filter 3 clear"
        include, exclude = parsed
        if len(include) + len(exclude) > MAX_FILTER_WORDS:
            return f"That's too many words (max {MAX_FILTER_WORDS})."
        self._store.set_filters(chat_id, sub.subscription_id, include_words=include, exclude_words=exclude)
        updated = self._find(chat_id, str(sub.subscription_id))
        return "Filter saved.\n" + _describe(updated) + "\nWords match whole words only (\"scrap\" does not match \"scraping\")."

    def _remove(self, chat_id: int, arg: str) -> str:
        sub = self._find(chat_id, arg) if arg else None
        if sub is None:
            return "Which search? Use the number from /searches, e.g. /remove 3"
        self._store.remove_subscription(chat_id, sub.subscription_id)
        return f"Stopped watching #{sub.subscription_id}: {sub.query_text}"

    def _pause(self, chat_id: int, arg: str) -> str:
        n = self._store.set_chat_enabled(chat_id, False)
        return f"Paused {n} search(es). /resume to start again." if n else "Nothing to pause."

    def _resume(self, chat_id: int, arg: str) -> str:
        n = self._store.set_chat_enabled(chat_id, True)
        return f"Resumed {n} search(es)." if n else "Nothing was paused."

    def _status(self, chat_id: int, arg: str) -> str:
        mine = self._store.subscriptions_for_chat(chat_id)
        active = sum(1 for s in mine if s.enabled)
        by_id = {s["search_id"]: s for s in self._store.snapshot()["searches"]}
        failing = [s for s in mine if s.enabled and by_id.get(s.search_id, {}).get("consecutive_failures", 0) >= 3]
        health = "Some searches are having trouble right now; I'm retrying." if failing else "Everything is running."
        return f"{active} active search(es), {len(mine) - active} paused.\n{health}" + self._shadow_note()


async def run_bot(core: BotCore, token: str, *, stop: Callable[[], bool] = lambda: False,
                  on_loop: Callable[[], None] = lambda: None) -> None:
    from aiogram import Bot, Dispatcher, F
    from aiogram.types import Message

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(F.text)
    async def on_text(msg: Message) -> None:
        # The store is synchronous; keep the event loop free while it talks to Postgres.
        reply = await asyncio.to_thread(core.handle, int(msg.chat.id), msg.text or "")
        if reply:
            await msg.answer(reply, disable_web_page_preview=True)

    async def housekeeping() -> None:
        while not stop():
            on_loop()
            await asyncio.sleep(2)
        await dp.stop_polling()

    # Commands sent while the bot was offline are dropped, not answered late.
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    log.info("bot_ready", extra={"username": me.username})
    keeper = asyncio.create_task(housekeeping())
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        keeper.cancel()
        await bot.session.close()
