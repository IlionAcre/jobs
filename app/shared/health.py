from __future__ import annotations

import os
from typing import Iterable

import redis
from sqlalchemy import Engine, text


def require_env(keys: Iterable[str]) -> None:
    missing = [k for k in keys if not os.environ.get(k, "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")


def check_db(engine: Engine) -> None:
    with engine.begin() as con:
        con.execute(text("SELECT 1"))


def check_redis(client: redis.Redis) -> None:
    client.ping()
