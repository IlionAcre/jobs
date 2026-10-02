"""
Health of the pipeline as data.

  collect(runtime) -> HealthSnapshot   reads Postgres + Redis; never raises (an unreachable service is a fact)
  evaluate(snapshot, config) -> [Problem]   pure: the rules that decide what counts as a problem

The watchdog (Telegram alerts), the dashboard and `scraper status` all use the same two functions, so they
can't disagree about whether something is wrong.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.scraper.config import ScraperConfig
from app.scraper.models import Token
from app.scraper.presence import RoleBeat
from app.scraper.queue import DISPATCHERS, FETCHERS, JOBS_STREAM, WORK_STREAM
from app.scraper.ratelimit import API

REQUIRED_ROLES = ("scheduler", "fetcher", "dispatcher")
CRITICAL, WARNING = "critical", "warning"


@dataclass(frozen=True, slots=True)
class Problem:
    key: str  # stable identity, used to tell "still open" from "new"
    severity: str
    message: str
    title: str = ""  # short name, used when announcing that it is resolved


@dataclass(slots=True)
class HealthSnapshot:
    at: float
    db_error: Optional[str] = None
    backend_error: Optional[str] = None  # Redis (or whatever backs queue/token/limits)
    roles: List[RoleBeat] = field(default_factory=list)
    token: Optional[Token] = None
    searches: List[Dict[str, Any]] = field(default_factory=list)
    work_backlog: int = 0
    jobs_backlog: int = 0
    api_paused_s: float = 0.0
    jobs_total: int = 0
    backup_checked: bool = False  # true when backups are enabled and the folder was looked at
    backup_age_s: Optional[float] = None  # age of the newest dump; None = there is none


def collect(runtime) -> HealthSnapshot:
    snap = HealthSnapshot(at=time.time())
    if runtime.config.backup.enabled:
        from app.scraper.backup import latest_backup_age_s
        try:
            snap.backup_age_s = latest_backup_age_s(runtime.config.backup, snap.at)
            snap.backup_checked = True
        except OSError:
            pass
    try:
        store_snap = runtime.store.snapshot()
        snap.searches = store_snap["searches"]
        snap.jobs_total = store_snap["jobs_total"]
    except Exception as ex:  # noqa: BLE001
        snap.db_error = repr(ex)[:300]
    try:
        snap.roles = runtime.presence.alive()
        snap.token = runtime.tokens.current()
        snap.work_backlog = runtime.queue.backlog(WORK_STREAM, FETCHERS)
        snap.jobs_backlog = runtime.queue.backlog(JOBS_STREAM, DISPATCHERS)
        snap.api_paused_s = runtime.limiter.paused_for(API)
    except Exception as ex:  # noqa: BLE001
        snap.backend_error = repr(ex)[:300]
    return snap


def daily_summary(snap: HealthSnapshot, problems: List[Problem], activity: Optional[Dict[str, Any]], config: ScraperConfig) -> str:
    """The once-a-day message: what happened in the last 24 hours and what is open now."""
    lines = ["📊 Scraper daily summary"]
    if activity is None:
        lines.append("• Activity is unknown: Postgres is not reachable.")
    else:
        d = activity["deliveries"]
        sent = d.get("sent", 0) + d.get("shadow", 0)
        extra = ", ".join(f"{d[k]} {k}" for k in ("filtered", "duplicate") if d.get(k))
        lines.append(f"• {activity['new_jobs']} new jobs, {sent} alerts" + (f" ({extra})" if extra else "")
                     + ("" if config.dispatcher.mode == "live" else " — shadow mode, nothing was sent"))
        if activity["lag_median_s"] is not None:
            lines.append(f"• Seen {activity['lag_median_s']:.0f} s after publish (median), {activity['lag_p90_s']:.0f} s (p90)")
    watched = [s for s in snap.searches if s["enabled"] and s["subscribers"]]
    lines.append(f"• {len(watched)} searches watched, roles running: {', '.join(sorted({b.role for b in snap.roles})) or 'unknown'}")
    if snap.token is not None:
        lines.append(f"• Token {snap.token.age_s(snap.at) / 3600:.1f} h old, from {snap.token.minter}")
    if problems:
        lines.append("Open problems:")
        lines.extend(f"• [{p.severity}] {p.message}" for p in problems)
    else:
        lines.append("No open problems.")
    return "\n".join(lines)


def _age_s(ts: Optional[datetime], now: float) -> Optional[float]:
    return None if ts is None else now - ts.astimezone(timezone.utc).timestamp()


def evaluate(snap: HealthSnapshot, config: ScraperConfig) -> List[Problem]:
    problems: List[Problem] = []
    if snap.backup_checked:
        limit_h = config.backup.max_age_hours
        if snap.backup_age_s is None:
            problems.append(Problem("backup_missing", WARNING, "There is no database backup yet.", "no database backup"))
        elif snap.backup_age_s > limit_h * 3600:
            problems.append(Problem("backup_stale", WARNING,
                                    f"The newest database backup is {snap.backup_age_s / 3600:.0f} h old (expected one every day).",
                                    "database backup overdue"))
    if snap.db_error:
        problems.append(Problem("db_down", CRITICAL, f"Postgres is not reachable: {snap.db_error}", "Postgres unreachable"))
    if snap.backend_error:
        problems.append(Problem("backend_down", CRITICAL, f"Redis is not reachable: {snap.backend_error}", "Redis unreachable"))
        return problems  # roles, token and queues can't be judged without it

    running = {b.role for b in snap.roles}
    for role in REQUIRED_ROLES:
        if role not in running:
            problems.append(Problem(f"role_missing:{role}", CRITICAL, f"The {role} is not running.", f"{role} not running"))

    watched = [s for s in snap.searches if s["enabled"] and s["subscribers"]]
    stale_after = config.alerts.stale_search_minutes * 60
    for s in watched:
        label = f"search #{s['search_id']} ({s['query']!r})"
        age = _age_s(s["last_ok_at"], snap.at)
        if s["consecutive_failures"] >= config.alerts.failing_search_after:
            problems.append(Problem(f"search_failing:{s['search_id']}", CRITICAL,
                                    f"{label} failed {s['consecutive_failures']} polls in a row. Last error: {(s['last_error'] or '')[:160]}",
                                    f"{label} failing"))
        elif age is not None and age > stale_after:
            problems.append(Problem(f"search_stale:{s['search_id']}", CRITICAL,
                                    f"{label} has had no successful poll for {age / 60:.0f} min.", f"{label} not being polled"))
        elif age is None and (_age_s(s.get("created_at"), snap.at) or 0) > stale_after:
            problems.append(Problem(f"search_stale:{s['search_id']}", CRITICAL, f"{label} has never been polled successfully.",
                                    f"{label} not being polled"))

    if snap.token is not None:
        primary = config.minters[0].name
        if snap.token.minter != primary:
            problems.append(Problem("token_fallback", WARNING,
                                    f"The token came from the fallback minter '{snap.token.minter}', not '{primary}'. "
                                    f"The primary is probably being challenged (stale browser profile?).",
                                    "token from a fallback minter"))
        age_h = snap.token.age_s(snap.at) / 3600
        if age_h > config.tokens.max_age_hours:
            problems.append(Problem("token_old", WARNING, f"The token is {age_h:.1f} h old and was not refreshed.", "token not refreshed"))

    if snap.api_paused_s > 0:
        problems.append(Problem("rate_limited", WARNING, f"Polling is paused for {snap.api_paused_s:.0f} s (rate limit or no token).",
                                "polling paused"))
    for name, size in (("work", snap.work_backlog), ("jobs", snap.jobs_backlog)):
        if size > config.alerts.queue_backlog_max:
            problems.append(Problem(f"backlog:{name}", WARNING, f"The {name} queue has {size} unprocessed items.", f"{name} queue backlog"))
    return problems
