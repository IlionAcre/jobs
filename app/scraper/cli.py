"""
`python main.py scraper <command>`

  up           start every role together and keep them running
  scheduler    run only the scheduler   (run exactly one)
  fetcher      run only a fetcher       (run as many as the request budget allows)
  dispatcher   run only a dispatcher
  watchdog     run only the health watchdog (Telegram alerts to the admin chat)
  dashboard    run only the status page (http://127.0.0.1:8787 by default)
  bot          run only the Telegram bot (/search, /filter, ...)
  seed         subscribe a chat to a search
  filter       show or change a subscription's include/exclude words
  searches     list searches and their subscribers
  mint         get a token now (--all tries every minter and reports each)
  status       show what the pipeline is doing right now
  cleanup      delete rows older than the retention window
  shadow-report  compare what this pipeline saw with the legacy monitor (upwork.jobs)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Callable, List, Optional

from dotenv import load_dotenv

from app.scraper.config import REPO_ROOT, ScraperConfig, load_scraper_config
from app.scraper.errors import ScraperError
from app.shared.logging_utils import configure_logging

log = logging.getLogger("scraper.cli")
CORE_ROLES = ("scheduler", "fetcher", "dispatcher")
ROLES = CORE_ROLES + ("watchdog", "dashboard", "bot")
PARENT_PID_ENV = "SCRAPER_SUPERVISOR_PID"


_LOG_HANDLE: Optional[IO[str]] = None  # set by `up --log-file`; handed to every role process


def _redirect_output(path: str) -> None:
    """Send stdout/stderr of this process, and of every child it starts, to `path` (append).

    Done here rather than with shell redirection (`>> file`) because a file opened by the Windows shell
    cannot be opened for writing by anyone else: one lingering process then blocks every relaunch.
    Python opens files shareable, so any number of processes can append to the same log.
    """
    global _LOG_HANDLE
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = _LOG_HANDLE = open(target, "a", buffering=1, encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    os.dup2(handle.fileno(), 1)
    os.dup2(handle.fileno(), 2)
    sys.stdout = open(1, "w", buffering=1, encoding="utf-8", closefd=False)
    sys.stderr = open(2, "w", buffering=1, encoding="utf-8", closefd=False)


def _consumer_name(role: str) -> str:
    return f"{role}-{socket.gethostname()}-{os.getpid()}"


def _sender(config: ScraperConfig) -> Callable[[int, str], None]:
    """Telegram sender; only created when something will actually be sent."""
    needs_telegram = config.dispatcher.mode == "live" or config.dispatcher.admin_chat_id is not None
    if not needs_telegram:
        return lambda chat_id, text: None
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required for dispatcher.mode=live or dispatcher.admin_chat_id")
    from app.notify.telegram import TelegramNotifier
    return TelegramNotifier(token=token).send


def _owner_chat_id() -> Optional[int]:
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def _alerts_chat_id(config: ScraperConfig) -> Optional[int]:
    """Where health alerts go: alerts.chat_id, else ALERTS_CHAT_ID (.env), else the owner's chat."""
    if config.alerts.chat_id is not None:
        return config.alerts.chat_id
    raw = os.environ.get("ALERTS_CHAT_ID", "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else _owner_chat_id()


def _state_file(name: str):
    """A tiny JSON file under logs/ for state that must survive a restart. Returns (load, save)."""
    path = REPO_ROOT / "logs" / name

    def load() -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(state: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    return load, save


def _telegram_token(why: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(f"TELEGRAM_BOT_TOKEN is required for {why}")
    return token


def enabled_roles(config: ScraperConfig) -> List[str]:
    """What `up` starts."""
    roles = list(CORE_ROLES) + ["dashboard"]
    if config.alerts.enabled:
        roles.append("watchdog")
    if config.bot.enabled:
        roles.append("bot")
    return roles


def _run_role(role: str, runtime, stop: Callable[[], bool] = lambda: False) -> None:
    from app.scraper.presence import Heartbeat

    cfg = runtime.config
    name = _consumer_name(role)
    beat = Heartbeat(runtime.presence, role, name)  # tells the watchdog and dashboard this role is alive

    if role == "scheduler":
        from app.scraper.pipeline.scheduler import Scheduler
        Scheduler(runtime.store, runtime.queue, cfg).run_forever(stop, beat)
    elif role == "fetcher":
        from app.scraper.pipeline.fetcher import Fetcher
        Fetcher(runtime.store, runtime.queue, runtime.search_client(), runtime.limiter, cfg).run_forever(name, stop, beat)
    elif role == "dispatcher":
        from app.scraper.pipeline.dispatcher import Dispatcher
        Dispatcher(runtime.store, runtime.queue, cfg, _sender(cfg)).run_forever(name, stop, beat)
    elif role == "watchdog":
        from app.notify.telegram import TelegramNotifier
        from app.scraper.health import collect, daily_summary, evaluate
        from app.scraper.pipeline.watchdog import Watchdog

        chat_id = _alerts_chat_id(cfg)
        if chat_id is None:
            raise SystemExit("watchdog needs alerts.chat_id in scraper.yaml, or ALERTS_CHAT_ID / TELEGRAM_CHAT_ID in .env")
        notifier = TelegramNotifier(token=_telegram_token("the watchdog"))

        def summary() -> str:
            snap = collect(runtime)
            try:
                activity = runtime.store.activity(24)
            except Exception:  # noqa: BLE001  (Postgres down: the summary says so)
                activity = None
            return daily_summary(snap, evaluate(snap, cfg), activity, cfg)

        load, save = _state_file("watchdog_state.json")
        Watchdog(lambda: collect(runtime), cfg, lambda text: notifier.send(chat_id, text),
                 summary=summary, load_state=load, save_state=save).run_forever(stop, beat)
    elif role == "dashboard":
        from app.scraper.dashboard import serve
        serve(runtime, stop, beat)
    elif role == "bot":
        import asyncio

        from app.scraper.bot import BotCore, build_policy, run_bot

        from app.notify.telegram import TelegramNotifier

        owner, token = _owner_chat_id(), _telegram_token("the bot")
        alerts_chat = _alerts_chat_id(cfg)
        notifier = TelegramNotifier(token=token)
        core = BotCore(runtime.store, cfg, build_policy(cfg, owner, runtime.store), owner_chat_id=owner,
                       notify_owner=(lambda text: notifier.send(alerts_chat, text)) if alerts_chat is not None else None)
        asyncio.run(run_bot(core, token, stop=stop, on_loop=beat, owner_chat_id=owner))
    else:
        raise SystemExit(f"unknown role {role!r}")


def _runtime(config: ScraperConfig):
    from app.scraper.factory import Runtime
    return Runtime(config)


def _parent_gone() -> Callable[[], bool]:
    """When started by `up`, a role exits if the supervisor dies, so a hard-killed `up` leaves no orphans.
    (Checked between queue reads, i.e. within a few seconds.)"""
    parent = os.environ.get(PARENT_PID_ENV)
    if not parent:
        return lambda: False
    import psutil
    pid = int(parent)

    def gone() -> bool:
        if psutil.pid_exists(pid):
            return False
        log.warning("supervisor_gone_exiting", extra={"supervisor_pid": pid})
        return True

    return gone


def cmd_role(args, config: ScraperConfig) -> int:
    code = 0
    try:
        _run_role(args.command, _runtime(config), _parent_gone())
    except KeyboardInterrupt:
        log.info("role_stopped", extra={"role": args.command})
    except SystemExit as ex:  # a startup requirement is missing (e.g. no Telegram token)
        print(ex, file=sys.stderr)
        code = 2
    except Exception:  # noqa: BLE001
        log.exception("role_crashed", extra={"role": args.command})
        code = 1
    finally:
        # A role is finished the moment its loop ends. Helpers it started (the Telegram sender's worker
        # thread, the dashboard's HTTP server) can leave a non-daemon thread blocked forever, and a normal
        # interpreter shutdown would wait for it: the process would linger as an orphan that still holds
        # the log file open, which on Windows stops the start script from relaunching the pipeline.
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return code  # not reached; keeps the signature honest for callers and tests that patch os._exit


def cmd_up(args, config: ScraperConfig) -> int:
    """All roles on this machine. With Redis each role is its own process (restarted if it exits);
    with the memory backend they are threads sharing one in-process queue."""
    if config.backend == "memory":
        runtime = _runtime(config)
        stop = threading.Event()
        threads = [threading.Thread(target=_run_role, args=(r, runtime, stop.is_set), name=r, daemon=True)
                   for r in enabled_roles(config)]
        for t in threads:
            t.start()
        try:
            while all(t.is_alive() for t in threads):
                time.sleep(1)
            log.error("role_thread_died", extra={"alive": [t.name for t in threads if t.is_alive()]})
            return 1
        except KeyboardInterrupt:
            stop.set()
            return 0

    main_py = str(REPO_ROOT / "main.py")
    children: dict = {}
    roles = enabled_roles(config)
    restarts = {r: 0 for r in roles}

    child_env = {**os.environ, PARENT_PID_ENV: str(os.getpid())}

    def start(role: str) -> None:
        out = {"stdout": _LOG_HANDLE, "stderr": subprocess.STDOUT} if _LOG_HANDLE is not None else {}
        children[role] = subprocess.Popen([sys.executable, "-u", main_py, "scraper", role],
                                          cwd=str(REPO_ROOT), env=child_env, **out)
        log.info("role_started", extra={"role": role, "pid": children[role].pid})

    for role in roles:
        start(role)
    try:
        while True:
            time.sleep(2)
            for role, child in list(children.items()):
                code = child.poll()
                if code is not None:
                    restarts[role] += 1
                    wait = min(60, 2 ** min(restarts[role], 6))
                    log.error("role_exited_restarting", extra={"role": role, "exit_code": code, "restart": restarts[role], "wait_s": wait})
                    time.sleep(wait)
                    start(role)
    except KeyboardInterrupt:
        log.info("up_stopping")
        for child in children.values():
            child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        return 0


def cmd_seed(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store

    chat_id = args.chat or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise SystemExit("give --chat or set TELEGRAM_CHAT_ID")
    store = build_store()
    search_id = store.upsert_search(args.query, poll_seconds=args.poll_seconds)
    sub_id = store.subscribe(int(chat_id), search_id, include_words=args.include or [], exclude_words=args.exclude or [])
    print(f"search {search_id}: {args.query!r}\nsubscription {sub_id}: chat {chat_id}"
          f"{' include=' + str(args.include) if args.include else ''}{' exclude=' + str(args.exclude) if args.exclude else ''}")
    return 0


def cmd_mint(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_minters

    if not args.all:
        runtime = _runtime(config)
        current = runtime.tokens.current()
        if args.force and current is not None:
            runtime.tokens.invalidate(current.value)
        value = runtime.tokens.get()
        token = runtime.tokens.current()
        print(f"token ok ({len(value)} chars), minted by {token.minter}, age {(time.time() - token.minted_at) / 60:.1f} min")
        return 0
    failed = 0
    for minter in build_minters(config):
        t0 = time.time()
        try:
            value = minter.mint()
            print(f"{minter.name:<14} OK    {time.time() - t0:5.1f}s  ({len(value)} chars)")
        except ScraperError as ex:
            failed += 1
            print(f"{minter.name:<14} FAIL  {time.time() - t0:5.1f}s  {type(ex).__name__}: {str(ex)[:140]}")
        time.sleep(3)
    return 1 if failed else 0


def _ago(ts: Optional[datetime]) -> str:
    if ts is None:
        return "never"
    s = int((datetime.now(timezone.utc) - ts).total_seconds())
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


def cmd_status(args, config: ScraperConfig) -> int:
    from app.scraper.health import CRITICAL, collect, evaluate

    runtime = _runtime(config)
    health = collect(runtime)
    problems = evaluate(health, config)
    if health.db_error:
        print("Postgres is not reachable:", health.db_error)
        return 1
    snap = runtime.store.snapshot()
    token = health.token
    info = {
        "backend": config.backend,
        "dispatcher_mode": config.dispatcher.mode,
        "token": None if token is None else {"minter": token.minter, "age_hours": round((time.time() - token.minted_at) / 3600, 2),
                                             "refresh_after_hours": config.tokens.refresh_after_hours},
        "queues": {"work_backlog": health.work_backlog, "jobs_backlog": health.jobs_backlog},
        "roles": sorted({b.role for b in health.roles}),
        "problems": [{"key": p.key, "severity": p.severity, "message": p.message} for p in problems],
        "jobs_total": snap["jobs_total"], "jobs_last_hour": snap["jobs_last_hour"],
        "deliveries_last_hour": snap["deliveries_last_hour"],
        "searches": snap["searches"],
    }
    bad_ids = {int(p.key.split(":")[1]) for p in problems if p.key.startswith(("search_stale:", "search_failing:"))}
    unhealthy = [s for s in snap["searches"] if s["search_id"] in bad_ids]
    if args.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        t = info["token"]
        print(f"backend: {info['backend']}   dispatcher: {info['dispatcher_mode']}")
        print("roles:   " + (", ".join(info["roles"]) or "none running"))
        print("token:   " + ("none (will be minted on the first poll)" if t is None else
                             f"minted by {t['minter']}, {t['age_hours']} h old (refresh at {t['refresh_after_hours']} h)"))
        print(f"queues:  work backlog {info['queues']['work_backlog']}, jobs backlog {info['queues']['jobs_backlog']}")
        print(f"jobs:    {info['jobs_total']} stored, {info['jobs_last_hour']} first seen in the last hour")
        print(f"alerts (last hour): {info['deliveries_last_hour'] or 'none'}")
        print(f"\n{'id':>3}  {'last ok':<10} {'fails':>5}  {'subs':>4}  {'primed':<6}  query")
        for s in snap["searches"]:
            flag = "  <-- STALE" if s in unhealthy else ""
            print(f"{s['search_id']:>3}  {_ago(s['last_ok_at']):<10} {s['consecutive_failures']:>5}  {s['subscribers']:>4}  "
                  f"{'yes' if s['primed'] else 'no':<6}  {s['query']}{'' if s['enabled'] else '  (disabled)'}{flag}")
            if s["consecutive_failures"] and s["last_error"]:
                print(f"       last error: {s['last_error'][:150]}")
        if not snap["searches"]:
            print("  (no searches yet: python main.py scraper seed --query \"python OR scraping\")")
        if problems:
            print("\nproblems:")
            for p in problems:
                print(f"  [{p.severity}] {p.message}")
        else:
            print("\nno problems")
    return 1 if any(p.severity == CRITICAL for p in problems) else 0


def cmd_filter(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store

    chat_id = args.chat or _owner_chat_id()
    if chat_id is None:
        raise SystemExit("give --chat or set TELEGRAM_CHAT_ID")
    store = build_store()
    mine = store.subscriptions_for_chat(int(chat_id))
    sub = next((s for s in mine if s.subscription_id == args.subscription), None)
    if sub is None:
        raise SystemExit(f"chat has no subscription {args.subscription}; it has: {[s.subscription_id for s in mine]}")
    if args.clear:
        store.set_filters(int(chat_id), sub.subscription_id, include_words=[], exclude_words=[])
    elif args.include is not None or args.exclude is not None:
        store.set_filters(int(chat_id), sub.subscription_id,
                          include_words=[w.lower() for w in (args.include if args.include is not None else sub.include_words)],
                          exclude_words=[w.lower() for w in (args.exclude if args.exclude is not None else sub.exclude_words)])
    sub = next(s for s in store.subscriptions_for_chat(int(chat_id)) if s.subscription_id == args.subscription)
    print(f"subscription {sub.subscription_id}: {sub.query_text!r}\n  include: {list(sub.include_words) or 'anything'}\n  exclude: {list(sub.exclude_words) or 'nothing'}")
    return 0


def cmd_searches(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store

    store = build_store()
    for s in store.snapshot()["searches"]:
        print(f"search {s['search_id']}: {s['query']!r} enabled={s['enabled']} subscribers={s['subscribers']}")
        for sub in store.subscriptions_for_search(s["search_id"]):
            print(f"    subscription {sub.subscription_id}: chat {sub.chat_id} include={list(sub.include_words)} exclude={list(sub.exclude_words)}")
    return 0


def cmd_cleanup(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store

    print(f"deleted (older than {config.retention_days} days):", build_store().cleanup(config.retention_days))
    return 0


def cmd_shadow_report(args, config: ScraperConfig) -> int:
    from app.scraper.shadow import shadow_report
    from app.scraper.store import normalize_query
    from app.store.db import build_db_dsn_from_env, make_engine

    r = shadow_report(make_engine(build_db_dsn_from_env()), query_norm=normalize_query(args.query), hours=args.hours)
    code = 1 if r["missed"] else 0  # non-zero when the legacy monitor alerted on something this pipeline did not
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return code
    lead, lag = r["new_pipeline_lead_over_legacy"], r["new_pipeline_seen_after_publish"]
    print(f"comparison with the legacy monitor for {r['query']!r} since {r['since']:%Y-%m-%d %H:%M}")
    print(f"  new jobs seen by both: {r['seen_by_both']}   only legacy monitor: {r['only_legacy']}   only new pipeline: {r['only_new']}")
    print(f"  of those seen by both: alerted {r['alerted']}, held back by a filter {r['held_back_by_filter']}, "
          f"seen but NOT alerted {r['seen_but_not_alerted']}")
    print(f"  MISSED (legacy had it, this pipeline did not alert): {r['missed']}")
    if lead["n"]:
        print(f"  new pipeline saw them first in {r['new_pipeline_first_in']}/{lead['n']}; lead over legacy: "
              f"median {lead['median_s']}s (p10 {lead['p10_s']}s, p90 {lead['p90_s']}s)")
    if lag["n"]:
        print(f"  new pipeline seen-after-publish: median {lag['median_s']}s, p90 {lag['p90_s']}s, min {lag['min_s']}s, max {lag['max_s']}s (n={lag['n']})")
    for j in r["only_legacy_jobs"]:
        print(f"  ONLY LEGACY: {j['legacy_seen']:%m-%d %H:%M:%S} {j['job_id']} {(j['title'] or '')[:70]}".encode("ascii", "replace").decode())
    for j in r["not_alerted_jobs"]:
        print(f"  NOT ALERTED: {j['new_seen']:%m-%d %H:%M:%S} {j['job_id']} {(j['title'] or '')[:70]}".encode("ascii", "replace").decode())
    for j in r["only_new_jobs"]:
        print(f"  only new:    {j['new_seen']:%m-%d %H:%M:%S} {j['job_id']}")
    return code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py scraper", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to scraper.yaml (default: app/config/scraper.yaml or $SCRAPER_CONFIG_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--log-file", help="append all output (this process and every role) to this file")
    for role in ROLES:
        sub.add_parser(role)
    seed = sub.add_parser("seed")
    seed.add_argument("--query", required=True)
    seed.add_argument("--chat", help="Telegram chat id (default: TELEGRAM_CHAT_ID from .env)")
    seed.add_argument("--include", nargs="*", help="alert only if one of these whole words/phrases appears")
    seed.add_argument("--exclude", nargs="*", help="never alert if one of these appears")
    seed.add_argument("--poll-seconds", type=int, help="override the default interval for this search")
    mint = sub.add_parser("mint")
    mint.add_argument("--all", action="store_true", help="try every configured minter and report each")
    mint.add_argument("--force", action="store_true", help="discard the current token first")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    sub.add_parser("searches")
    flt = sub.add_parser("filter")
    flt.add_argument("subscription", type=int, help="subscription id (see `scraper searches`)")
    flt.add_argument("--chat", type=int, help="chat that owns it (default: TELEGRAM_CHAT_ID)")
    flt.add_argument("--include", nargs="*", help="alert only if one of these whole words/phrases appears")
    flt.add_argument("--exclude", nargs="*", help="never alert if one of these appears")
    flt.add_argument("--clear", action="store_true", help="remove the filter")
    report = sub.add_parser("shadow-report")
    report.add_argument("--query", default="python OR scraping", help="the search both monitors run")
    report.add_argument("--hours", type=float, default=24.0)
    report.add_argument("--json", action="store_true")
    sub.add_parser("cleanup")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    if args.command == "up" and args.log_file:
        _redirect_output(args.log_file)
    if args.command in ROLES or args.command == "up":
        configure_logging()
    config = load_scraper_config(args.config)

    handlers = {"up": cmd_up, "seed": cmd_seed, "mint": cmd_mint, "status": cmd_status,
                "searches": cmd_searches, "filter": cmd_filter, "cleanup": cmd_cleanup, "shadow-report": cmd_shadow_report, **{r: cmd_role for r in ROLES}}
    return handlers[args.command](args, config)
