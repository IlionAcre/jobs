from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from app.settings import load_bot_settings
from app.shared.health import check_db, require_env
from app.shared.logging_utils import configure_logging, get_logger
from app.store.core import (
    PLATFORM_UPWORK,
    create_auth_link,
    create_or_get_query_for_user,
    ensure_core_schema_and_tables,
    get_portal_user_id,
    is_subscription_active,
    list_subscriptions_for_chat,
    remove_subscription,
    set_subscription_enabled,
    subscribe_chat_to_query,
    upsert_chat,
    upsert_user_seen,
)
from app.store.db import build_db_dsn_from_env, make_pg_engine


DEFAULT_POLL_SECONDS = 60
DEFAULT_TOP_N = 25

HELP = (
    "Commands:\n"
    "/start\n"
    "/query <expression>\n"
    "/queries\n"
    "/remove <sub_id>\n"
    "/pause\n"
    "/resume\n"
    "/status\n\n"
    "Query examples:\n"
    "  /query python AND scraping\n"
    "  /query python OR javascript AND selenium\n"
)


def _chat_title(msg: Message) -> Optional[str]:
    t = getattr(msg.chat, "title", None)
    return str(t) if t else None


def _compile_query(raw: str) -> str:
    """
    Minimal AND/OR compiler:
      - AND is treated as whitespace
      - OR is preserved

    Example:
      "python AND scraping" -> "python scraping"
      "python OR scraping AND selenium" -> "python OR scraping selenium"
    """
    s = " ".join(raw.strip().split())
    tokens = s.split()

    out = []
    for tok in tokens:
        up = tok.upper()
        if up == "AND":
            continue
        if up == "OR":
            out.append("OR")
            continue
        out.append(tok)

    return " ".join(out).strip()


async def main() -> None:
    load_dotenv()
    configure_logging()
    log = get_logger("bot")

    require_env(["DB_NAME", "DB_USER", "DB_PASSWORD", "TELEGRAM_BOT_TOKEN"])
    settings = load_bot_settings()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)
    check_db(engine)

    bot = Bot(token=token)
    dp = Dispatcher()

    async def gate_or_prompt(msg: Message) -> Optional[str]:
        """
        Returns portal_user_id if user is linked + subscription active.
        Otherwise sends a prompt and returns None.
        """
        chat_id = int(msg.chat.id)
        telegram_user_id = int(msg.from_user.id) if msg.from_user else 0

        upsert_chat(engine, chat_id, str(msg.chat.type), _chat_title(msg))

        if msg.from_user:
            upsert_user_seen(
                engine,
                telegram_user_id,
                username=msg.from_user.username,
                first_name=msg.from_user.first_name,
                last_name=msg.from_user.last_name,
            )

        portal_user_id = get_portal_user_id(engine, telegram_user_id)

        # Not linked: generate one-time link token
        if not portal_user_id:
            link_token = secrets.token_urlsafe(24)
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

            create_auth_link(
                engine,
                token=link_token,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                expires_at=expires_at,
            )

            link_url = f"{settings.portal_base_url}/link?token={link_token}"
            await msg.answer(
                "🔐 You’re not linked yet.\n"
                "Login/link your Telegram here:\n"
                f"{link_url}\n\n"
                "After linking, send /start again."
            )
            return None

        # Linked but subscription inactive
        if not is_subscription_active(engine, portal_user_id):
            subscribe_url = f"{settings.portal_base_url}/subscribe"
            await msg.answer(
                "💳 Your subscription is not active.\n"
                "Subscribe here:\n"
                f"{subscribe_url}\n\n"
                "After subscribing, send /start again."
            )
            return None

        return portal_user_id

    @dp.message(Command("start"))
    async def cmd_start(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        rows = list_subscriptions_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK)
        if not rows:
            await msg.answer(
                "✅ Linked + active.\n"
                "No queries yet.\n\n"
                "Create one with:\n"
                "/query python AND scraping\n\n"
                + HELP
            )
            return

        await msg.answer(f"✅ Linked + active.\nSubscriptions in this chat: {len(rows)}\n\nUse /queries")

    @dp.message(Command("status"))
    async def cmd_status(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        rows = list_subscriptions_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK)
        await msg.answer(f"✅ Active.\nSubscriptions in this chat: {len(rows)}")

    @dp.message(Command("queries"))
    async def cmd_queries(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        rows = list_subscriptions_for_chat(engine, int(msg.chat.id), platform=PLATFORM_UPWORK)
        if not rows:
            await msg.answer("No queries yet. Use /query <expression>.")
            return

        lines = ["📌 Queries for this chat:"]
        for r in rows:
            status = "✅" if r['sub_enabled'] else "⏸️"
            lines.append(
                f"{status} #{r['subscription_id']} | "
                f"poll={r['poll_seconds']}s | top={r['top_n']}\n"
                f"   {r.get('raw_text') or r.get('compiled_query')!r}"
            )
        await msg.answer("\n".join(lines))

    @dp.message(Command("query"))
    async def cmd_query(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await msg.answer("Usage: /query <expression>\nExample: /query python AND scraping")
            return

        raw_text = parts[1].strip()
        compiled = _compile_query(raw_text)

        query_id = create_or_get_query_for_user(
            engine,
            portal_user_id=portal_user_id,
            platform=PLATFORM_UPWORK,
            raw_text=raw_text,
            compiled_query=compiled,
            poll_seconds=DEFAULT_POLL_SECONDS,
            top_n=DEFAULT_TOP_N,
            quote_terms=False,
        )
        sub_id = subscribe_chat_to_query(engine, int(msg.chat.id), query_id)

        await msg.answer(
            f"✅ Query #{sub_id} created!\n"
            f"Search: {raw_text}\n\n"
            "The scheduler will start monitoring for new jobs."
        )

    @dp.message(Command("remove"))
    async def cmd_remove(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await msg.answer("Usage: /remove <sub_id>\nExample: /remove 123")
            return

        try:
            sub_id = int(parts[1].strip())
        except ValueError:
            await msg.answer("⚠️ Invalid subscription ID. Must be a number.")
            return

        removed = remove_subscription(engine, int(msg.chat.id), sub_id)
        if removed:
            await msg.answer(f"✅ Subscription #{sub_id} removed.")
        else:
            await msg.answer(f"⚠️ Subscription #{sub_id} not found in this chat.")

    @dp.message(Command("pause"))
    async def cmd_pause(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        count = set_subscription_enabled(engine, int(msg.chat.id), enabled=False)
        if count > 0:
            await msg.answer(f"⏸️ Paused {count} subscription(s) in this chat.")
        else:
            await msg.answer("No subscriptions to pause.")

    @dp.message(Command("resume"))
    async def cmd_resume(msg: Message) -> None:
        portal_user_id = await gate_or_prompt(msg)
        if not portal_user_id:
            return

        count = set_subscription_enabled(engine, int(msg.chat.id), enabled=True)
        if count > 0:
            await msg.answer(f"▶️ Resumed {count} subscription(s) in this chat.")
        else:
            await msg.answer("No subscriptions to resume.")

    @dp.message(Command("help"))
    async def cmd_help(msg: Message) -> None:
        await msg.answer(HELP)

    # Fallback: plain text becomes a query
    @dp.message(F.text)
    async def fallback_text(msg: Message) -> None:
        txt = (msg.text or "").strip()
        if not txt or txt.startswith("/"):
            return
        msg.text = f"/query {txt}"
        await cmd_query(msg)

    log.info("bot_ready")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

