from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "upwork"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def config_dict() -> dict:
    """A small valid config; tests mutate copies of it."""
    return {
        "upwork": {
            "search_page_url": "https://www.upwork.com/nx/search/jobs/",
            "api_url": "https://www.upwork.com/api/graphql/v1?alias=visitorJobSearch",
            "token_cookie": "UniversalSearchNuxt_vt",
            "job_url_template": "https://www.upwork.com/jobs/{job_id}",
        },
        "transports": {
            "curl_cffi": {"kind": "curl_cffi", "impersonate": "chrome150"},
            "requests_h1": {"kind": "requests_h1"},
        },
        "poller": {"transport": "curl_cffi", "interval_s": 30, "jitter": 0.3, "ids_count": 10,
                   "details_count": 5, "max_job_age_minutes": 3},
        "tokens": {"refresh_after_hours": 10, "max_age_hours": 14},
        "minters": [{"name": "curl_cffi", "kind": "http", "transport": "curl_cffi"}],
        "failure_policy": {"transient_retries": 3, "transient_backoff_s": [5, 20, 60], "rate_limit_backoff_s": 120},
        "rate_limit": {"api_per_minute": 60, "page_per_10min": 20},
        "backend": "memory",
        "dispatcher": {"mode": "shadow"},
        "retention_days": 30,
    }


# --- database-backed fixtures: real Postgres from .env, one throwaway schema per test ---------

@pytest.fixture(scope="session")
def engine():
    import os

    from dotenv import load_dotenv
    from sqlalchemy import text

    from app.store.db import build_db_dsn_from_env, make_engine

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if not all(os.environ.get(k) for k in ("DB_NAME", "DB_USER", "DB_PASSWORD")):
        pytest.skip("database not configured (.env)")
    eng = make_engine(build_db_dsn_from_env())
    try:
        with eng.connect() as con:
            con.execute(text("SELECT 1"))
    except Exception as ex:  # noqa: BLE001
        pytest.skip(f"database not reachable: {ex!r}")
    yield eng
    eng.dispose()


@pytest.fixture
def schema(engine):
    import uuid

    from sqlalchemy import text

    name = f"scraper_test_{uuid.uuid4().hex[:10]}"
    yield name
    with engine.begin() as con:
        con.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))


@pytest.fixture
def store(engine, schema):
    from app.scraper.store import ScraperStore

    s = ScraperStore(engine, schema)
    s.create_tables()
    return s
