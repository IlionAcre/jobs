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
import time
from collections import deque
from typing import Callable, Deque, Dict, Iterable, List, Optional, Protocol, Tuple

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


class StoredAllowlistPolicy:
    """The config's allowlist plus the chats the owner admitted with /allow (kept in Postgres)."""

    def __init__(self, chat_ids: Iterable[int], store: ScraperStore) -> None:
        self._static = AllowlistPolicy(chat_ids)
        self._store = store

    def allowed(self, chat_id: int) -> bool:
        return self._static.allowed(chat_id) or self._store.chat_is_allowed(chat_id)


def build_policy(config: ScraperConfig, owner_chat_id: Optional[int], store: Optional[ScraperStore] = None) -> AccessPolicy:
    if config.bot.access == "open":
        return OpenPolicy()
    static = [*config.bot.allowed_chat_ids, *([owner_chat_id] if owner_chat_id is not None else [])]
    return StoredAllowlistPolicy(static, store) if store is not None else AllowlistPolicy(static)


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


OWNER_HELP = (
    "\n\nOwner only:\n"
    "/allow 123456789 name — let that chat use the bot\n"
    "/deny 123456789 — take it away (their searches are paused)\n"
    "/allowed — who has access"
)


class BotCore:
    def __init__(self, store: ScraperStore, config: ScraperConfig, policy: AccessPolicy, *,
                 owner_chat_id: Optional[int] = None, notify_owner: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._store = store
        self._cfg = config
        self._policy = policy
        self._owner = owner_chat_id
        self._notify_owner = notify_owner  # tells the owner what they should know (e.g. a stranger knocked)
        self._clock = clock
        self._recent: Dict[int, Deque[float]] = {}  # chat -> times of its last commands
        self._announced: set = set()  # strangers the owner was already told about

    def _too_fast(self, chat_id: int) -> bool:
        now = self._clock()
        times = self._recent.setdefault(chat_id, deque())
        while times and now - times[0] > 60:
            times.popleft()
        times.append(now)
        return len(times) > self._cfg.bot.commands_per_minute

    def _tell_owner(self, text: str) -> None:
        if self._notify_owner is None:
            return
        try:
            self._notify_owner(text)
        except Exception:  # noqa: BLE001
            log.exception("bot_notify_owner_failed")

    def handle(self, chat_id: int, text: str, who: str = "") -> Optional[str]:
        """Reply to one message, or None to stay silent (not a command). `who` describes the sender."""
        text = (text or "").strip()
        if not text.startswith("/"):
            return None
        head, _, arg = text.partition(" ")
        command = head[1:].split("@", 1)[0].lower()  # "/search@MyBot" -> "search"
        arg = arg.strip()

        if self._too_fast(chat_id):
            return "Slow down a little — try again in a minute."

        if not self._policy.allowed(chat_id):
            if chat_id not in self._announced:
                self._announced.add(chat_id)
                log.info("bot_access_refused", extra={"chat_id": chat_id})
                self._tell_owner(f"🔑 Someone tried the bot: {who or 'unknown'} (chat {chat_id}).\n"
                                 f"To let them in, send me: /allow {chat_id}")
            return ("This bot is private for now. Ask the owner to allow this chat.\n"
                    f"Chat id: {chat_id}")

        handler = {
            "start": self._help, "help": self._help,
            "search": self._search, "query": self._search,
            "searches": self._searches, "queries": self._searches,
            "filter": self._filter, "remove": self._remove,
            "pause": self._pause, "resume": self._resume, "status": self._status,
        }.get(command)
        if handler is None and self._owner is not None and chat_id == self._owner:
            handler = {"allow": self._allow, "deny": self._deny, "allowed": self._allowed}.get(command)
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
        return HELP + (OWNER_HELP if self._owner is not None and chat_id == self._owner else "") + self._shadow_note()

    # --- owner commands --------------------------------------------------------------------------

    @staticmethod
    def _chat_arg(arg: str) -> Tuple[Optional[int], str]:
        token, _, rest = arg.partition(" ")
        return (int(token), rest.strip()) if token.lstrip("-").isdigit() else (None, "")

    def _allow(self, chat_id: int, arg: str) -> str:
        target, note = self._chat_arg(arg)
        if target is None:
            return "Which chat? /allow 123456789 name"
        new = self._store.allow_chat(target, note[:80])
        self._announced.discard(target)
        return f"Chat {target} can now use the bot." if new else f"Chat {target} was already allowed."

    def _deny(self, chat_id: int, arg: str) -> str:
        target, _ = self._chat_arg(arg)
        if target is None:
            return "Which chat? /deny 123456789"
        if target == self._owner:
            return "That's you."
        removed = self._store.deny_chat(target)
        if not removed:
            in_config = target in set(self._cfg.bot.allowed_chat_ids)
            return f"Chat {target} was not on the list" + (" (it is allowed in the config file)." if in_config else ".")
        paused = self._store.set_chat_enabled(target, False)  # no alerts for a chat that can no longer manage them
        return f"Chat {target} can no longer use the bot; {paused} search(es) paused."

    def _allowed(self, chat_id: int, arg: str) -> str:
        rows = self._store.allowed_chats()
        static = sorted(set(self._cfg.bot.allowed_chat_ids))
        lines = [f"{r['chat_id']}  {r['note'] or ''}".rstrip() for r in rows]
        text = "Allowed with /allow:\n" + ("\n".join(lines) if lines else "nobody yet")
        if static:
            text += "\nIn the config file: " + ", ".join(str(c) for c in static)
        return text + "\nYou are always allowed."

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
                  on_loop: Callable[[], None] = lambda: None, owner_chat_id: Optional[int] = None) -> None:
    from aiogram import Bot, Dispatcher, F
    from aiogram.types import ChatMemberUpdated, Message

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(F.text)
    async def on_text(msg: Message) -> None:
        user = msg.from_user
        who = " ".join(x for x in [user.full_name if user else "", f"@{user.username}" if user and user.username else ""] if x)
        # The store is synchronous; keep the event loop free while it talks to Postgres.
        reply = await asyncio.to_thread(core.handle, int(msg.chat.id), msg.text or "", who)
        if reply:
            await msg.answer(reply, disable_web_page_preview=True)

    @dp.my_chat_member()
    async def on_membership(update: ChatMemberUpdated) -> None:
        # The bot was added to (or removed from) a group or channel. The owner needs that chat's id to
        # point alerts at it, and Telegram shows it nowhere else.
        chat = update.chat
        status = getattr(update.new_chat_member.status, "value", str(update.new_chat_member.status))
        log.info("bot_membership_changed", extra={"chat_id": chat.id, "chat_type": str(chat.type), "status": status})
        if chat.type != "private" and owner_chat_id is not None:
            await bot.send_message(owner_chat_id, f"I am now '{status}' in the {chat.type} \"{chat.title}\".\nIts chat id: {chat.id}")

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
        await dp.start_polling(bot, handle_signals=False, allowed_updates=["message", "my_chat_member"])
    finally:
        keeper.cancel()
        await bot.session.close()
