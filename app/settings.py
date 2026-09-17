from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerSettings:
    stream: str
    group: str
    interval_s: int


@dataclass(frozen=True)
class WorkerSettings:
    stream: str
    group: str
    consumer: str
    dlq_stream: str
    max_attempts: int
    autoclaim_idle_ms: int
    read_count: int
    block_ms: int


@dataclass(frozen=True)
class BotSettings:
    portal_base_url: str


def _as_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def load_scheduler_settings() -> SchedulerSettings:
    return SchedulerSettings(
        stream=os.environ.get("UPWORK_STREAM", "work:upwork"),
        group=os.environ.get("UPWORK_GROUP", "workers:upwork"),
        interval_s=_as_int("SCHEDULER_INTERVAL_S", 5),
    )


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        stream=os.environ.get("UPWORK_STREAM", "work:upwork"),
        group=os.environ.get("UPWORK_GROUP", "workers:upwork"),
        consumer=os.environ.get("UPWORK_CONSUMER", "worker-1"),
        dlq_stream=os.environ.get("UPWORK_DLQ_STREAM", "work:upwork:dlq"),
        max_attempts=_as_int("UPWORK_MAX_ATTEMPTS", 3),
        autoclaim_idle_ms=_as_int("UPWORK_AUTOCLAIM_IDLE_MS", 60_000),
        read_count=_as_int("UPWORK_READ_COUNT", 10),
        block_ms=_as_int("UPWORK_BLOCK_MS", 5000),
    )


def load_bot_settings() -> BotSettings:
    return BotSettings(
        portal_base_url=os.environ.get("PORTAL_BASE_URL", "https://example.com").rstrip("/"),
    )
