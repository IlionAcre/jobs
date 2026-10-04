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
  backup       dump the database to the backup folder and prune old dumps
  shadow-report  compare what this pipeline alerted on with the legacy monitor (upwork.jobs)
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


def rotate_log(path: Path, *, max_bytes: int, keep: int) -> bool:
    """file -> file.1 -> file.2 ... (at most `keep` old files) once `path` passes `max_bytes`.

    Done at start-up, before the file is opened. If something still has it open (Windows refuses the
    rename), rotation is skipped this time rather than failing the start.
    """
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
        path.with_name(f"{path.name}.{keep}").unlink(missing_ok=True)
        for n in range(keep - 1, 0, -1):
            older = path.with_name(f"{path.name}.{n}")
            if older.exists():
                older.replace(path.with_name(f"{path.name}.{n + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


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
    notifier = TelegramNotifier(token=token)
    return lambda chat_id, text: notifier.send(chat_id, text, html=True)  # alerts are Telegram HTML


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


def is_supervisor_cmdline(cmdline: List[str]) -> bool:
    """True for `python [-u] main.py scraper up [...]`."""
    names = [Path(part).name.lower() for part in cmdline]
    if "main.py" not in names:
        return False
    rest = cmdline[names.index("main.py") + 1:]
    return rest[:2] == ["scraper", "up"]


def other_supervisor() -> Optional[int]:
    """Pid of another running `scraper up`, if any. This process and its launcher are not counted
    (a Windows venv's python.exe is a small launcher whose child is the real interpreter)."""
    import psutil

    mine = {os.getpid(), os.getppid()}
    for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            info = proc.info
            if info["pid"] in mine or info["ppid"] in mine or not info["cmdline"]:
                continue
            if is_supervisor_cmdline(info["cmdline"]):
                return int(info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


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

    other = other_supervisor()
    if other is not None:
        # Two pipelines would poll twice, fight over the bot's connection and double the request rate.
        log.error("another_supervisor_running", extra={"pid": other})
        print(f"another `scraper up` is already running (pid {other}); not starting a second one", file=sys.stderr)
        return 3

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


def cmd_backup(args, config: ScraperConfig) -> int:
    from app.scraper.backup import run_backup

    if not config.backup.enabled:
        print("backups are disabled (backup.enabled: false)")
        return 0
    try:
        result = run_backup(config.backup, os.environ)
    except subprocess.CalledProcessError as ex:
        print(f"backup failed: {ex.cmd[0]} exited {ex.returncode}: {(ex.stderr or '').strip()[:400]}", file=sys.stderr)
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"backup failed: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1
    print(f"backup written: {result['file']} ({result['bytes'] / 1e6:.1f} MB), pruned {result['pruned']}"
          + (f", copied to {result['copied_to']}" if result["copied_to"] else ""))
    return 0


def cmd_firehose(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store
    from app.scraper.firehose import FIREHOSE_QUERY

    store = build_store()
    if args.off:
        sid = store.upsert_search(FIREHOSE_QUERY)
        store.set_search_enabled(sid, False)
        print(f"all-jobs collector (search {sid}) stopped; its data is kept")
        return 0
    sid = store.upsert_search(FIREHOSE_QUERY, poll_seconds=args.poll_seconds, always_poll=True, ids_count=args.page)
    print(f"all-jobs collector is search {sid}: every {args.poll_seconds} s, {args.page} newest jobs per poll, "
          "no subscribers (nothing is sent). Report: python main.py scraper firehose-report")
    return 0


def cmd_firehose_report(args, config: ScraperConfig) -> int:
    from app.scraper.factory import build_store
    from app.scraper.firehose import report
    from app.store.db import build_db_dsn_from_env, make_engine

    r = report(make_engine(build_db_dsn_from_env()), build_store(), query=args.query, hours=args.hours)
    if args.json or "error" in r:
        print(json.dumps(r, indent=2, default=str))
        return 1 if "error" in r else 0
    w, v, sp, c, g = r["window"], r["volume"], r["speed_seen_after_publish"], r["completeness"], r["granularity"]
    fmt = lambda d: "no data" if not d["n"] else f"median {d['median_s']} s, p10 {d['p10_s']}, p90 {d['p90_s']}, min {d['min_s']}, max {d['max_s']} (n={d['n']})"  # noqa: E731
    utc = lambda t: t.astimezone(timezone.utc)  # noqa: E731  (Postgres returns the session's time zone)
    out = [f"all-jobs experiment, {utc(w['since']):%m-%d %H:%M} → {utc(w['until']):%m-%d %H:%M} UTC ({w['hours']} h)",
           "", "VOLUME",
           f"  {v['jobs']} new jobs, {v['per_hour_avg']}/hour on average"
           + (f"; busiest hour {utc(v['busiest_hour'][0]):%m-%d %H}:00 UTC with {v['busiest_hour'][1]}" if v["busiest_hour"] else ""),
           f"  most new jobs in a single poll: {v['most_new_in_one_poll']}",
           "", "SPEED (publish → first seen)",
           f"  collector:  {fmt(sp['collector'])}", f"  per-search: {fmt(sp['per_search'])}",
           "", f"COMPLETENESS (jobs the per-search polling found for {g['query']!r})",
           f"  {c['also_seen_by_collector']} of {c['found_by_per_search']} also seen by the collector; "
           f"collector first in {c['collector_first_in']}; lead {fmt(c['collector_seen_first_by'])}"]
    out += [f"  MISSED by collector: {j}" for j in c["missed_by_collector"]]
    out += ["", f"GRANULARITY (our literal matcher on every collected job vs Upwork's search for {g['query']!r})",
            f"  Upwork returned {g['upwork_matched']}, local matcher {g['local_matched']}, both {g['both']}"]
    out += [f"  upwork only (lost by matching locally): {x['job_id']} {(x['title'] or '')[:70]}" for x in g["upwork_only"]]
    out += [f"  local only (extra, or missed by the per-search poll): {x['job_id']} {(x['title'] or '')[:70]}" for x in g["local_only"]]
    if g["collected_without_details"]:
        out.append(f"  ({g['collected_without_details']} collected jobs had no details stored)")
    print("\n".join(out).encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8"))
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
    sub.add_parser("backup")
    fire = sub.add_parser("firehose", help="switch the all-jobs collector on (default) or off")
    fire.add_argument("--off", action="store_true")
    fire.add_argument("--poll-seconds", type=int, default=10)
    fire.add_argument("--page", type=int, default=50, help="newest jobs looked at per poll (max 50)")
    fire_report = sub.add_parser("firehose-report")
    fire_report.add_argument("--query", default="python OR scraping", help="the per-search query to compare with")
    fire_report.add_argument("--hours", type=float, default=24.0)
    fire_report.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    config = load_scraper_config(args.config)
    if args.command == "up" and args.log_file:
        rotate_log(Path(args.log_file), max_bytes=int(config.log.max_mb * 1e6), keep=config.log.keep_files)
        _redirect_output(args.log_file)
    if args.command in ROLES or args.command == "up":
        configure_logging()

    handlers = {"up": cmd_up, "seed": cmd_seed, "mint": cmd_mint, "status": cmd_status, "backup": cmd_backup,
                "firehose": cmd_firehose, "firehose-report": cmd_firehose_report,
                "searches": cmd_searches, "filter": cmd_filter, "cleanup": cmd_cleanup, "shadow-report": cmd_shadow_report, **{r: cmd_role for r in ROLES}}
    return handlers[args.command](args, config)
