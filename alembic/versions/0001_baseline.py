"""baseline core and upwork schemas

Revision ID: 0001_baseline
Revises:
Create Date: 2026-02-22 14:30:00
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "core";')
    op.execute('CREATE SCHEMA IF NOT EXISTS "upwork";')

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".chats (
            chat_id     BIGINT PRIMARY KEY,
            chat_type   TEXT NOT NULL DEFAULT 'unknown',
            title       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        '''
    )

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".users (
            telegram_user_id BIGINT PRIMARY KEY,
            portal_user_id   TEXT UNIQUE,
            username         TEXT,
            first_name       TEXT,
            last_name        TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        '''
    )

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".auth_links (
            token            TEXT PRIMARY KEY,
            telegram_user_id BIGINT NOT NULL REFERENCES "core".users(telegram_user_id) ON DELETE CASCADE,
            chat_id          BIGINT NOT NULL REFERENCES "core".chats(chat_id) ON DELETE CASCADE,
            expires_at       TIMESTAMPTZ NOT NULL,
            used_at          TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        '''
    )
    op.execute('CREATE INDEX IF NOT EXISTS auth_links_expires_used_idx ON "core".auth_links (expires_at, used_at);')

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".portal_subscriptions (
            portal_user_id TEXT PRIMARY KEY,
            status         TEXT NOT NULL DEFAULT 'inactive',
            valid_until    TIMESTAMPTZ,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        '''
    )
    op.execute('CREATE INDEX IF NOT EXISTS portal_subscriptions_status_idx ON "core".portal_subscriptions (status);')

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".queries (
            query_id      BIGSERIAL PRIMARY KEY,
            portal_user_id TEXT,
            raw_text        TEXT,
            compiled_query  TEXT,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            next_run_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            platform      TEXT NOT NULL,
            terms_raw     TEXT NOT NULL DEFAULT '',
            terms_norm    TEXT NOT NULL DEFAULT '',
            quote_terms   BOOLEAN NOT NULL DEFAULT FALSE,
            poll_seconds  INT NOT NULL DEFAULT 60,
            top_n         INT NOT NULL DEFAULT 25,
            qhash         TEXT NOT NULL DEFAULT '',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        '''
    )
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS queries_qhash_uq ON "core".queries (qhash);')
    op.execute('CREATE INDEX IF NOT EXISTS queries_due_idx ON "core".queries (platform, enabled, next_run_at);')
    op.execute('CREATE INDEX IF NOT EXISTS queries_portal_user_idx ON "core".queries (portal_user_id);')

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".subscriptions (
            subscription_id BIGSERIAL PRIMARY KEY,
            chat_id         BIGINT NOT NULL REFERENCES "core".chats(chat_id) ON DELETE CASCADE,
            query_id        BIGINT NOT NULL REFERENCES "core".queries(query_id) ON DELETE CASCADE,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            primed_at       TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (chat_id, query_id)
        );
        '''
    )
    op.execute('CREATE INDEX IF NOT EXISTS subscriptions_enabled_idx ON "core".subscriptions (enabled);')

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "core".deliveries (
            subscription_id BIGINT NOT NULL REFERENCES "core".subscriptions(subscription_id) ON DELETE CASCADE,
            job_key         TEXT NOT NULL,
            delivered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (subscription_id, job_key)
        );
        '''
    )

    op.execute(
        '''
        CREATE TABLE IF NOT EXISTS "upwork".jobs (
            id BIGSERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            snippet TEXT,
            budget TEXT,
            location TEXT,
            job_type TEXT,
            duration TEXT,
            tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            raw JSONB NOT NULL DEFAULT '{}'::jsonb,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT jobs_source_url_ux UNIQUE (source, url)
        );
        '''
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "upwork".jobs;')
    op.execute('DROP TABLE IF EXISTS "core".deliveries;')
    op.execute('DROP TABLE IF EXISTS "core".subscriptions;')
    op.execute('DROP TABLE IF EXISTS "core".queries;')
    op.execute('DROP TABLE IF EXISTS "core".portal_subscriptions;')
    op.execute('DROP TABLE IF EXISTS "core".auth_links;')
    op.execute('DROP TABLE IF EXISTS "core".users;')
    op.execute('DROP TABLE IF EXISTS "core".chats;')
    op.execute('DROP SCHEMA IF EXISTS "upwork";')
    op.execute('DROP SCHEMA IF EXISTS "core";')
