"""scraper pipeline schema (searches, subscriptions, jobs, search_hits, deliveries)

Additive: creates the `scraper` schema only; `core` and `upwork` are not touched.
Table definitions live in app/scraper/store.py::schema_ddl so the migration and the code can't drift.

Revision ID: 0002_scraper
Revises: 0001_baseline
Create Date: 2026-10-01 17:00:00
"""
from __future__ import annotations

from alembic import op

from app.scraper.store import DEFAULT_SCHEMA, schema_ddl, schema_drop

revision = "0002_scraper"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in schema_ddl(DEFAULT_SCHEMA):
        op.execute(statement)


def downgrade() -> None:
    for statement in schema_drop(DEFAULT_SCHEMA):
        op.execute(statement)
