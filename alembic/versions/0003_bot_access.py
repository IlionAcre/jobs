"""bot_access: chats the owner allowed to use the bot

Additive. Table definitions live in app/scraper/store.py::schema_ddl, whose statements are all
IF NOT EXISTS, so running it again creates only what is new.

Revision ID: 0003_bot_access
Revises: 0002_scraper
Create Date: 2026-10-01 21:00:00
"""
from __future__ import annotations

from alembic import op

from app.scraper.store import DEFAULT_SCHEMA, schema_ddl

revision = "0003_bot_access"
down_revision = "0002_scraper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in schema_ddl(DEFAULT_SCHEMA):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{DEFAULT_SCHEMA}".bot_access')
