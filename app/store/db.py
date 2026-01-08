from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Engine, create_engine


def build_db_dsn_from_env() -> str:
    """
    Build SQLAlchemy DSN from discrete .env variables.

    Required:
      DB_NAME, DB_USER, DB_PASSWORD

    Optional:
      DB_HOST (default: localhost)
      DB_PORT (default: 5432)
      DB_DRIVER (default: psycopg)
    """
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ["DB_NAME"]
    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    driver = os.environ.get("DB_DRIVER", "psycopg")  # psycopg / pg8000 / psycopg2

    return f"postgresql+{driver}://{user}:{password}@{host}:{port}/{name}"


def make_pg_engine(dsn: str, *, pool_pre_ping: bool = True) -> Engine:
    """
    Create a SQLAlchemy Engine for Postgres.
    """
    return create_engine(dsn, pool_pre_ping=pool_pre_ping)
