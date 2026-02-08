"""
Scheduler: Polls PostgreSQL for due queries and pushes them to Redis Streams.

This is the missing piece that produces work items for the workers to consume.
Run with: python main.py scheduler
"""
from __future__ import annotations

import os
import time
from typing import List

from dotenv import load_dotenv

from app.queue.redis_streams import ensure_consumer_group, get_redis_client, xadd_work
from app.store.core import PLATFORM_UPWORK, bump_next_run, ensure_core_schema_and_tables, list_due_queries
from app.store.db import build_db_dsn_from_env, make_pg_engine


# Config from environment
STREAM = os.environ.get("UPWORK_STREAM", "work:upwork")
GROUP = os.environ.get("UPWORK_GROUP", "workers:upwork")
SCHEDULER_INTERVAL_S = int(os.environ.get("SCHEDULER_INTERVAL_S", "5"))


def main() -> None:
    load_dotenv()

    print("[scheduler] starting...")

    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)

    r = get_redis_client()
    ensure_consumer_group(r, stream=STREAM, group=GROUP)

    print(f"[scheduler] stream={STREAM} group={GROUP}")
    print(f"[scheduler] polling every {SCHEDULER_INTERVAL_S}s for due queries")

    try:
        while True:
            try:
                due_query_ids: List[int] = list_due_queries(engine, platform=PLATFORM_UPWORK, limit=100)

                if due_query_ids:
                    print(f"[scheduler] found {len(due_query_ids)} due queries")

                for qid in due_query_ids:
                    # Push to Redis Stream
                    msg_id = xadd_work(
                        r,
                        stream=STREAM,
                        fields={"query_id": str(qid)},
                    )
                    print(f"[scheduler] enqueued query_id={qid} -> msg_id={msg_id}")

                    # Bump next_run_at so it won't be picked up again until poll_seconds later
                    bump_next_run(engine, query_id=qid)

                time.sleep(SCHEDULER_INTERVAL_S)

            except KeyboardInterrupt:
                print("\n[scheduler] stopped.")
                return

            except Exception as e:
                print(f"[scheduler] error: {e!r} (retrying in {SCHEDULER_INTERVAL_S}s)")
                time.sleep(SCHEDULER_INTERVAL_S)

    finally:
        try:
            r.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
