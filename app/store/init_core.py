from __future__ import annotations

from dotenv import load_dotenv

from app.store.core import ensure_core_schema_and_tables
from app.store.db import build_db_dsn_from_env, make_pg_engine


def main() -> None:
    load_dotenv()
    engine = make_pg_engine(build_db_dsn_from_env())
    ensure_core_schema_and_tables(engine)
    print("[init_core] core schema + tables ensured.")


if __name__ == "__main__":
    main()
