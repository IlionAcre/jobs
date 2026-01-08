from __future__ import annotations

import asyncio
import os
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from app.store.core import (
    PLATFORM_UPWORK,
    create_or_get_query,
    ensure_core_schema_and_tables,
    list_subscriptions_for_chat,
    remove_subscription,
    set_subscription_enabled,
    subscribe_chat_to_query,
    update_query_settings_for_chat,
    upsert_chat,
)
from app.store.db import build_db_dsn_from_env, make_pg_engine


DEFAULT_POLL_SECONDS: int = 60
DEFAULT_TOP_N: int = 25


HELP_TEXT = (
    "Upwork monitor bot:\n\n"
    "/add <terms...>     Add a query (example: /add python scraping)\n"
    "/list               List your subscriptions\n"
    "/remove <sub_id>    Remove a subscription (from /list)\n"
    "/interval <sec>     Set poll interval for ALL your Upwork queries\n"
    "/top <n>            Set top N for ALL your Upwork queries\n"
    "/stop               Disable all your subscriptions\n"
    "/resume             Enable all your subscriptions\n"
    "/status             Show quick status\n"
)


def _chat_title(msg: Message) -> Optional[str]:
    # For groups/channels
    t = getattr(msg.chat, "title", None)
    return str(t) if t else None


async def main() -> None:
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(msg: Message) -> None:
        upsert_chat(engine, int(msg.chat.id), str(msg.chat.type), _chat_title(msg))
        await msg.answer("✅ Connected.\n\n" + HELP_TEXT)

    @dp.message(Command("help"))
    async def cmd_help(msg: Message) -> None:
        await msg.answer(HELP_TEXT)

    @dp.message(Command("add"))
    async def cmd_add(msg: Message) -> None:
        upsert_chat(engine, int(msg.chat.id), str(msg.chat.type), _chat_title(msg))

        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await msg.answer("Usage: /add <terms...>\nExample: /add python scraping")
            return

        terms_raw = parts[1].strip()

        # NOTE: quote_terms is currently controlled by your app/config/config.yaml for scraping.
        # For now, store quote_terms=False in DB; you can extend UI later.
        quote_terms = False

        qid = create_or_get_query(
            engine,
            platform=PLATFORM_UPWORK,
            terms_raw=terms_raw,
            quote_terms=quote_terms,
            poll_seconds=DEFAULT_POLL_SECONDS,
            top_n=DEFAULT_TOP_N,
        )
        sub_id = subscribe_chat_to_query(engine, int(msg.chat.id), qid)

        await msg.answer(f"✅ Added subscription #{sub_id}\nQuery: {terms_raw!r}")

    @dp.message(Command("list"))
    async def cmd_list(msg: Message) -> None:
        upsert_chat(engine, int(msg.chat.id), str(msg.chat.type), _chat_title(msg))

        rows = list_subscriptions_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK)
        if not rows:
            await msg.answer("No subscriptions yet. Use /add python scraping")
            return

        lines = ["📌 Your subscriptions:"]
        for r in rows:
            lines.append(
                f"- sub_id={r['subscription_id']} | enabled={r['enabled']} | "
                f"interval={r['poll_seconds']}s | top={r['top_n']} | terms={r['terms_raw']!r}"
            )
        await msg.answer("\n".join(lines))

    @dp.message(Command("remove"))
    async def cmd_remove(msg: Message) -> None:
        parts = (msg.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await msg.answer("Usage: /remove <sub_id>\nGet sub_id from /list")
            return

        sid = int(parts[1])
        ok = remove_subscription(engine, int(msg.chat.id), sid)
        await msg.answer("✅ Removed." if ok else "Not found (or not yours).")

    @dp.message(Command("interval"))
    async def cmd_interval(msg: Message) -> None:
        parts = (msg.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await msg.answer("Usage: /interval <seconds>\nExample: /interval 60")
            return

        sec = int(parts[1])
        if sec < 15:
            await msg.answer("⚠️ 15 seconds minimum recommended.")
        n = update_query_settings_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK, poll_seconds=sec)
        await msg.answer(f"✅ Updated interval for {n} query(s) to {sec}s")

    @dp.message(Command("top"))
    async def cmd_top(msg: Message) -> None:
        parts = (msg.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await msg.answer("Usage: /top <n>\nExample: /top 25")
            return

        n_top = int(parts[1])
        n = update_query_settings_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK, top_n=n_top)
        await msg.answer(f"✅ Updated top_n for {n} query(s) to {n_top}")

    @dp.message(Command("stop"))
    async def cmd_stop(msg: Message) -> None:
        n = set_subscription_enabled(engine, int(msg.chat.id), enabled=False)
        await msg.answer(f"🛑 Disabled {n} subscription(s).")

    @dp.message(Command("resume"))
    async def cmd_resume(msg: Message) -> None:
        n = set_subscription_enabled(engine, int(msg.chat.id), enabled=True)
        await msg.answer(f"▶️ Enabled {n} subscription(s).")

    @dp.message(Command("status"))
    async def cmd_status(msg: Message) -> None:
        rows = list_subscriptions_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK)
        enabled = sum(1 for r in rows if r["enabled"])
        await msg.answer(f"Subscriptions: {len(rows)} total, {enabled} enabled.\n\nUse /list to view.")

    # Convenience: if they just type text, treat it like /add
    @dp.message(F.text)
    async def fallback_text(msg: Message) -> None:
        text_in = (msg.text or "").strip()
        if not text_in or text_in.startswith("/"):
            return
        msg.text = f"/add {text_in}"
        await cmd_add(msg)

    print("[bot] running. Ctrl+C to stop.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
