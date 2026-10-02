"""
Scheduler: Polls PostgreSQL for due queries and pushes them to Redis Streams.
Run with: python main.py scheduler
"""
from __future__ import annotations

import time
from typing import List

from dotenv import load_dotenv

from app.queue.redis_streams import ensure_consumer_group, get_redis_client, xadd_work
from app.settings import load_scheduler_settings
from app.shared.health import check_db, check_redis, require_env
from app.shared.logging_utils import configure_logging, get_logger
from app.store.core import PLATFORM_UPWORK, bump_next_run, ensure_core_schema_and_tables, list_due_queries
from app.store.db import build_db_dsn_from_env, make_pg_engine


def main() -> None:
    load_dotenv()
    configure_logging()
    log = get_logger("scheduler")

    require_env(["DB_NAME", "DB_USER", "DB_PASSWORD"])
    cfg = load_scheduler_settings()

    log.info("scheduler_starting")

    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)
    check_db(engine)

    r = get_redis_client()
    check_redis(r)
    ensure_consumer_group(r, stream=cfg.stream, group=cfg.group)

    log.info(
        "scheduler_ready",
        extra={
            "stream": cfg.stream,
            "group": cfg.group,
            "interval_s": cfg.interval_s,
        },
    )

    try:
        while True:
            try:
                due_query_ids: List[int] = list_due_queries(engine, platform=PLATFORM_UPWORK, limit=100)

                if due_query_ids:
                    log.info("scheduler_due_found", extra={"due_count": len(due_query_ids)})

                for qid in due_query_ids:
                    msg_id = xadd_work(
                        r,
                        stream=cfg.stream,
                        fields={"query_id": str(qid), "attempt": "0"},
                    )
                    log.info(
                        "scheduler_enqueued",
                        extra={"query_id": qid, "msg_id": msg_id, "stream": cfg.stream},
                    )

                    bump_next_run(engine, query_id=qid)

                time.sleep(cfg.interval_s)

            except KeyboardInterrupt:
                log.info("scheduler_stopped")
                return

            except Exception:
                log.exception("scheduler_loop_error", extra={"retry_in_s": cfg.interval_s})
                time.sleep(cfg.interval_s)

    finally:
        try:
            r.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
