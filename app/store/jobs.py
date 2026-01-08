from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.engine import Engine

_TABLE_CACHE: Dict[str, Table] = {}


def make_engine(dsn: str) -> Engine:
    return create_engine(dsn, pool_pre_ping=True)


def _jobs_table(schema: str) -> Table:
    if schema in _TABLE_CACHE:
        return _TABLE_CACHE[schema]

    md = MetaData()
    t = Table(
        "jobs",
        md,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("source", Text, nullable=False),
        Column("url", Text, nullable=False),
        Column("title", Text),
        Column("snippet", Text),
        Column("budget", Text),
        Column("location", Text),
        Column("job_type", Text),
        Column("duration", Text),
        Column("tags", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
        Column("raw", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("source", "url", name="jobs_source_url_ux"),
        schema=schema,
    )
    _TABLE_CACHE[schema] = t
    return t


def ensure_schema(engine: Engine, *, schema: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        _jobs_table(schema).metadata.create_all(conn, checkfirst=True)


def _normalize_tags(tags: Any) -> List[str]:
    if tags is None:
        return []
    if isinstance(tags, (list, tuple)):
        return [str(x) for x in tags if x is not None]
    if isinstance(tags, str):
        parts = [p.strip() for p in tags.split(",")]
        return [p for p in parts if p]
    return [str(tags)]


def job_to_record(job: Any) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "url": getattr(job, "url", None),
        "title": getattr(job, "title", None),
        "snippet": getattr(job, "snippet", None),
        "budget": getattr(job, "budget", None),
        "location": getattr(job, "location", None),
        "job_type": getattr(job, "job_type", None),
        "duration": getattr(job, "duration", None),
        "tags": _normalize_tags(getattr(job, "tags", None)),
        "raw": {},
    }
    if is_dataclass(job):
        try:
            rec["raw"] = asdict(job)
        except Exception:
            rec["raw"] = {}
    return rec


def upsert_job(engine: Engine, *, schema: str, source: str, job: Any) -> Tuple[bool, Optional[int]]:
    t = _jobs_table(schema)
    rec = job_to_record(job)

    if not rec["url"]:
        return (False, None)

    stmt = insert(t).values(source=source, **rec)
    inserted_new_expr = text("(xmax = 0) AS inserted_new")

    stmt = stmt.on_conflict_do_update(
        constraint="jobs_source_url_ux",
        set_={
            "title": stmt.excluded.title,
            "snippet": stmt.excluded.snippet,
            "budget": stmt.excluded.budget,
            "location": stmt.excluded.location,
            "job_type": stmt.excluded.job_type,
            "duration": stmt.excluded.duration,
            "tags": stmt.excluded.tags,
            "raw": stmt.excluded.raw,
            "last_seen_at": func.now(),
        },
    ).returning(t.c.id, inserted_new_expr)

    with engine.begin() as conn:
        row = conn.execute(stmt).first()

    if not row:
        return (False, None)

    return (bool(row[1]), int(row[0]))
