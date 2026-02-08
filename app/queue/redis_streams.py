from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Tuple

import redis


def get_redis_client() -> redis.Redis:
    url = os.environ.get("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    return redis.Redis(host=host, port=port, db=db, decode_responses=True)


def ensure_consumer_group(r: redis.Redis, *, stream: str, group: str) -> None:
    """
    Ensure stream + consumer group exist.
    Uses MKSTREAM so group creation works even if stream is empty.
    """
    try:
        r.xgroup_create(name=stream, groupname=group, id="0-0", mkstream=True)
    except redis.ResponseError as e:
        # BUSYGROUP means it already exists
        if "BUSYGROUP" in str(e):
            return
        raise


def xadd_work(
    r: redis.Redis,
    *,
    stream: str,
    fields: Dict[str, str],
    maxlen: int = 50_000,
    approximate: bool = True,
) -> str:
    """
    Add a work item to a stream. Keeps the stream bounded.
    """
    return r.xadd(stream, fields, maxlen=maxlen, approximate=approximate)


def xreadgroup(
    r: redis.Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 5000,
) -> List[Tuple[str, List[Tuple[str, Dict[str, str]]]]]:
    """
    Read new messages (">") from the stream as part of a consumer group.
    """
    return r.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )


def xack(r: redis.Redis, *, stream: str, group: str, msg_id: str) -> int:
    return int(r.xack(stream, group, msg_id))


def xautoclaim(
    r: redis.Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    min_idle_ms: int = 60_000,
    count: int = 50,
    start_id: str = "0-0",
) -> Tuple[str, List[Tuple[str, Dict[str, str]]]]:
    """
    Claim stale pending messages (Redis >= 6.2).
    Returns (next_start_id, messages)
    """
    # redis-py returns: (next_start_id, messages, deleted_ids?) depending on version
    res = r.xautoclaim(stream, group, consumer, min_idle_time=min_idle_ms, start_id=start_id, count=count)
    # Normalize (some versions include deleted_ids)
    if len(res) >= 2:
        next_id = res[0]
        msgs = res[1]
        return str(next_id), msgs
    return start_id, []
