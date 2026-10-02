"""The Redis implementations, against the real Redis (ops/redis). Skipped when it is not reachable."""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from app.scraper.models import Token
from app.scraper.presence import RedisPresence
from app.scraper.queue import RedisQueue
from app.scraper.ratelimit import API, RedisRateLimiter
from app.scraper.tokens.store import RedisTokenStore


@pytest.fixture(scope="module")
def redis_client():
    import redis

    client = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True, socket_connect_timeout=2)
    try:
        client.ping()
    except Exception as ex:  # noqa: BLE001
        if os.environ.get("REQUIRE_SERVICES"):  # CI: a missing service is a failure, not a skip
            raise
        pytest.skip(f"redis not reachable: {ex!r}")
    yield client
    client.close()


@pytest.fixture
def prefix(redis_client):
    name = f"test:{uuid.uuid4().hex[:10]}"
    yield name
    for key in redis_client.scan_iter(f"{name}*"):
        redis_client.delete(key)


def test_token_store_round_trip_and_conditional_delete(redis_client, prefix):
    store = RedisTokenStore(redis_client, prefix=prefix)
    assert store.get("default") is None
    token = Token("oauth2v2_abc", 123.5, "curl_cffi")
    store.put(token)
    assert store.get("default") == token and store.get("other-egress") is None
    store.delete("default", "oauth2v2_not_this_one")            # someone else already replaced it: no-op
    assert store.get("default") == token
    store.delete("default", "oauth2v2_abc")
    assert store.get("default") is None


def test_mint_lock_is_exclusive_and_released(redis_client, prefix):
    a, b = RedisTokenStore(redis_client, prefix=prefix), RedisTokenStore(redis_client, prefix=prefix)
    with a.lock("default", 30) as mine:
        assert mine is True
        with b.lock("default", 30) as theirs:
            assert theirs is False
    with b.lock("default", 30) as now_free:
        assert now_free is True


def test_rate_limiter_is_shared_between_instances_and_pause_works(redis_client, prefix):
    a = RedisRateLimiter(redis_client, prefix=prefix)
    b = RedisRateLimiter(redis_client, prefix=prefix)            # "another worker process"
    window = 3600.0                                              # one long window so the test can't straddle two
    assert a.acquire(API, 3, window) == 0 and b.acquire(API, 3, window) == 0 and a.acquire(API, 3, window) == 0
    assert b.acquire(API, 3, window) > 0                         # the 4th call, whoever makes it
    other_ip = RedisRateLimiter(redis_client, prefix=prefix, egress_id="ip2")
    assert other_ip.acquire(API, 3, window) == 0                 # budgets are per egress
    other_ip.pause(API, 5)
    assert 0 < other_ip.acquire(API, 3, window) <= 5


def test_queue_delivers_once_per_group_and_tracks_backlog(redis_client, prefix):
    q = RedisQueue(redis_client)
    stream = f"{prefix}:work"
    assert q.backlog(stream, "g") == 0
    q.put(stream, {"search_id": "1"})
    q.put(stream, {"search_id": "2"})
    assert q.backlog(stream, "g") == 2
    got = q.get(stream, "g", "c1", count=10, block_ms=200)
    assert [f["search_id"] for _, f in got] == ["1", "2"]
    assert q.get(stream, "g", "c2", count=10, block_ms=50) == []  # already handed to c1
    for msg_id, _ in got:
        q.ack(stream, "g", msg_id)
    assert q.backlog(stream, "g") == 0


def test_queue_hands_abandoned_work_to_another_consumer(redis_client, prefix):
    q = RedisQueue(redis_client, reclaim_idle_ms=50)
    stream = f"{prefix}:work"
    q.put(stream, {"search_id": "7"})
    assert len(q.get(stream, "g", "dead-worker", block_ms=200)) == 1   # read, never acked
    time.sleep(0.15)
    (msg_id, fields), = q.get(stream, "g", "rescuer", block_ms=200)
    assert fields == {"search_id": "7"}
    q.ack(stream, "g", msg_id)
    assert q.backlog(stream, "g") == 0


def test_queue_get_blocks_until_work_arrives(redis_client, prefix):
    q = RedisQueue(redis_client)
    stream = f"{prefix}:work"
    q.backlog(stream, "g")                                       # create the group first
    threading.Timer(0.2, lambda: q.put(stream, {"search_id": "9"})).start()
    t0 = time.time()
    got = q.get(stream, "g", "c", block_ms=3000)
    assert len(got) == 1 and time.time() - t0 < 2.5


def test_presence_lists_live_roles_and_expires_dead_ones(redis_client, prefix):
    presence = RedisPresence(redis_client, prefix=prefix)
    presence.beat("fetcher", "fetcher-a", {"polls": 4}, ttl_s=30)
    presence.beat("scheduler", "scheduler-a", ttl_s=1)
    alive = presence.alive()
    assert [(b.role, b.name) for b in alive] == [("fetcher", "fetcher-a"), ("scheduler", "scheduler-a")]
    assert alive[0].info == {"polls": 4} and abs(alive[0].at - time.time()) < 5
    time.sleep(1.3)
    assert [b.role for b in presence.alive()] == ["fetcher"]                 # the scheduler stopped beating


def test_rate_limiter_paused_for_is_read_only(redis_client, prefix):
    rl = RedisRateLimiter(redis_client, prefix=prefix)
    assert rl.paused_for(API) == 0.0
    rl.pause(API, 5)
    assert 0 < rl.paused_for(API) <= 5 and 0 < rl.paused_for(API) <= 5      # asking twice costs nothing
