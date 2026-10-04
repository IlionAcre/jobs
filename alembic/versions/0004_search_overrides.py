"""searches.always_poll and searches.ids_count (the all-jobs collector)

Additive. Table definitions live in app/scraper/store.py::schema_ddl, whose statements are all idempotent.

Revision ID: 0004_search_overrides
Revises: 0003_bot_access
Create Date: 2026-10-03 23:00:00
"""
from __future__ import annotations

from alembic import op

from app.scraper.store import DEFAULT_SCHEMA, schema_ddl

revision = "0004_search_overrides"
down_revision = "0003_bot_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in schema_ddl(DEFAULT_SCHEMA):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f'ALTER TABLE "{DEFAULT_SCHEMA}".searches DROP COLUMN IF EXISTS always_poll')
    op.execute(f'ALTER TABLE "{DEFAULT_SCHEMA}".searches DROP COLUMN IF EXISTS ids_count')
