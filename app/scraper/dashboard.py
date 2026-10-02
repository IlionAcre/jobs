"""
Read-only admin dashboard: one HTML page, a JSON endpoint and a health check.

  GET /            status page (refreshes itself every 15 s)
  GET /api/status  the same data as JSON
  GET /healthz     200 when no critical problem is open, 503 otherwise (for uptime monitors)

Built on the standard library so the pipeline needs no web framework. It binds to 127.0.0.1 by default and
exposes no secrets (the token value is never shown, chat ids are masked). With a password set
(`dashboard.password_env`) the page and the API ask for it; binding to any other address requires one.
It speaks plain HTTP, so reach it over a private network (Tailscale), never the open internet.
"""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

from app.scraper.config import ScraperConfig
from app.scraper.health import CRITICAL, REQUIRED_ROLES, HealthSnapshot, Problem, collect, evaluate

log = logging.getLogger("scraper.dashboard")


def _ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "never"
    s = int(max(0, seconds))
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


def _age(ts: Optional[datetime], now: float) -> Optional[float]:
    return None if ts is None else now - ts.astimezone(timezone.utc).timestamp()


def build_status(snap: HealthSnapshot, problems: List[Problem], config: ScraperConfig,
                 activity: Optional[Dict[str, Any]], recent: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Everything the page shows, as plain data (also served at /api/status)."""
    primary = config.minters[0].name
    token = None
    if snap.token is not None:
        token = {"minter": snap.token.minter, "is_primary": snap.token.minter == primary,
                 "age_hours": round(snap.token.age_s(snap.at) / 3600, 2),
                 "refresh_after_hours": config.tokens.refresh_after_hours}
    return {
        "at": datetime.fromtimestamp(snap.at, tz=timezone.utc).isoformat(timespec="seconds"),
        "ok": not any(p.severity == CRITICAL for p in problems),
        "problems": [{"key": p.key, "severity": p.severity, "message": p.message} for p in problems],
        "mode": config.dispatcher.mode,
        "backend": config.backend,
        "roles": [{"role": b.role, "name": b.name, "last_beat_s": round(snap.at - b.at, 1), "info": b.info} for b in snap.roles],
        "missing_roles": [r for r in REQUIRED_ROLES if r not in {b.role for b in snap.roles}],
        "token": token,
        "queues": {"work": snap.work_backlog, "jobs": snap.jobs_backlog},
        "api_paused_s": round(snap.api_paused_s, 1),
        "searches": [{"search_id": s["search_id"], "query": s["query"], "enabled": s["enabled"], "primed": s["primed"],
                      "subscribers": s["subscribers"], "consecutive_failures": s["consecutive_failures"],
                      "last_ok_s": None if _age(s["last_ok_at"], snap.at) is None else round(_age(s["last_ok_at"], snap.at)),
                      "last_error": s["last_error"] if s["consecutive_failures"] else None} for s in snap.searches],
        "jobs_total": snap.jobs_total,
        "activity": activity,
        "recent_jobs": [{"seen_at": j["seen_at"].astimezone(timezone.utc).isoformat(timespec="seconds") if j["seen_at"] else None,
                         "lag_s": None if j["lag_s"] is None else round(j["lag_s"]), "title": j["title"],
                         "url": j["url"], "query": j["query"]} for j in (recent or [])],
    }


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2330;--mute:#667085;--line:#e3e6eb;--ok:#12805c;--warn:#b25e09;--bad:#c0362c;--okbg:#e7f6ef;--warnbg:#fdf1e0;--badbg:#fdeceb}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ee;--mute:#98a2b3;--line:#2b3340;--ok:#4cc79a;--warn:#f0a94c;--bad:#f08379;--okbg:#14342a;--warnbg:#3a2a12;--badbg:#3d1c19}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 40px}h1{font-size:20px;margin:0 0 2px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mute);margin:0 0 10px}
.sub{color:var(--mute);margin-bottom:16px}.banner{padding:12px 14px;border-radius:10px;margin-bottom:16px;font-weight:600}
.banner.ok{background:var(--okbg);color:var(--ok)}.banner.bad{background:var(--badbg);color:var(--bad)}.banner.warn{background:var(--warnbg);color:var(--warn)}
.banner ul{margin:6px 0 0;padding-left:18px;font-weight:400}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.big{font-size:22px;font-weight:650}.mute{color:var(--mute)}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}.pill.ok{background:var(--okbg);color:var(--ok)}.pill.bad{background:var(--badbg);color:var(--bad)}.pill.warn{background:var(--warnbg);color:var(--warn)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:600;font-size:12px}td.wrap{white-space:normal;min-width:240px}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}a{color:inherit}
"""


def _pill(text: str, kind: str) -> str:
    return f'<span class="pill {kind}">{escape(text)}</span>'


def render_html(status: Dict[str, Any]) -> str:
    e = escape
    problems = status["problems"]
    if not problems:
        banner = '<div class="banner ok">All good — every role is running and every search is being polled.</div>'
    else:
        kind = "bad" if not status["ok"] else "warn"
        items = "".join(f"<li>{e(p['message'])}</li>" for p in problems)
        banner = f'<div class="banner {kind}">{len(problems)} open problem(s)<ul>{items}</ul></div>'

    roles = "".join(
        f"<tr><td>{e(r['role'])}</td><td class='mute'>{e(r['name'])}</td><td class='num'>{_ago(r['last_beat_s'])}</td></tr>"
        for r in status["roles"])
    roles += "".join(f"<tr><td>{e(r)}</td><td>{_pill('not running', 'bad')}</td><td></td></tr>" for r in status["missing_roles"])

    t = status["token"]
    token = ("<div class='big'>none</div><div class='mute'>minted on the first poll</div>" if t is None else
             f"<div class='big'>{t['age_hours']} h old</div><div class='mute'>from {e(t['minter'])} "
             f"{'' if t['is_primary'] else _pill('fallback', 'warn')} · refresh at {t['refresh_after_hours']} h</div>")

    a = status["activity"] or {}
    lag = "–" if a.get("lag_median_s") is None else f"{a['lag_median_s']:.0f} s"
    p90 = "–" if a.get("lag_p90_s") is None else f"{a['lag_p90_s']:.0f} s"
    deliveries = ", ".join(f"{v} {e(k)}" for k, v in sorted((a.get("deliveries") or {}).items())) or "none"

    searches = ""
    for s in status["searches"]:
        if not s["enabled"]:
            state = _pill("disabled", "warn")
        elif not s["subscribers"]:
            state = _pill("no subscribers", "warn")
        elif s["consecutive_failures"]:
            state = _pill(f"{s['consecutive_failures']} failed", "bad")
        elif not s["primed"]:
            state = _pill("starting", "warn")
        else:
            state = _pill("ok", "ok")
        err = f"<div class='mute'>{e((s['last_error'] or '')[:160])}</div>" if s["last_error"] else ""
        searches += (f"<tr><td class='num'>{s['search_id']}</td><td class='wrap'>{e(s['query'])}{err}</td><td>{state}</td>"
                     f"<td class='num'>{_ago(s['last_ok_s'])}</td><td class='num'>{s['subscribers']}</td></tr>")
    searches = searches or "<tr><td colspan='5' class='mute'>No searches yet.</td></tr>"

    jobs = ""
    for j in status["recent_jobs"]:
        title = e(j["title"] or "(details not stored)")
        link = f"<a href='{e(j['url'])}' rel='noreferrer' target='_blank'>{title}</a>" if j["url"] else title
        jobs += (f"<tr><td>{e((j['seen_at'] or '')[11:19])}</td><td class='num'>{'–' if j['lag_s'] is None else str(j['lag_s']) + ' s'}</td>"
                 f"<td class='wrap'>{link}</td><td class='mute'>{e(j['query'] or '')}</td></tr>")
    jobs = jobs or "<tr><td colspan='4' class='mute'>No new jobs seen yet.</td></tr>"

    mode = _pill("live", "ok") if status["mode"] == "live" else _pill("shadow — nothing is sent to subscribers", "warn")
    paused = f"<div class='mute'>polling paused {status['api_paused_s']} s</div>" if status["api_paused_s"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15"><title>Scraper status</title><style>{_CSS}</style></head><body><main>
<h1>Scraper pipeline</h1><div class="sub">{e(status['at'])} UTC · {mode} · backend {e(status['backend'])} · refreshes every 15 s</div>
{banner}
<div class="grid">
<div class="card"><h2>Token</h2>{token}</div>
<div class="card"><h2>Queues</h2><div class="big">{status['queues']['work']} / {status['queues']['jobs']}</div><div class="mute">work / new-job events waiting</div>{paused}</div>
<div class="card"><h2>New jobs, last {a.get('hours', 24):g} h</h2><div class="big">{a.get('new_jobs', 0)}</div><div class="mute">seen {lag} after publish (median), {p90} (p90)</div></div>
<div class="card"><h2>Alerts, last {a.get('hours', 24):g} h</h2><div class="big">{sum((a.get('deliveries') or {}).values())}</div><div class="mute">{deliveries}</div></div>
</div>
<div class="grid">
<div class="card"><h2>Roles</h2><div class="scroll"><table><tr><th>role</th><th>process</th><th class="num">last beat</th></tr>{roles}</table></div></div>
</div>
<div class="card" style="margin-bottom:12px"><h2>Searches</h2><div class="scroll"><table><tr><th class="num">id</th><th>query</th><th>state</th><th class="num">last ok</th><th class="num">subscribers</th></tr>{searches}</table></div></div>
<div class="card"><h2>Latest new jobs</h2><div class="scroll"><table><tr><th>seen (UTC)</th><th class="num">after publish</th><th>title</th><th>search</th></tr>{jobs}</table></div></div>
</main></body></html>"""


def current_status(runtime) -> Dict[str, Any]:
    snap = collect(runtime)
    activity = recent = None
    if snap.db_error is None:
        try:
            activity, recent = runtime.store.activity(24.0), runtime.store.recent_jobs(25)
        except Exception as ex:  # noqa: BLE001
            snap.db_error = repr(ex)[:300]
    return build_status(snap, evaluate(snap, runtime.config), runtime.config, activity, recent)


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def check_basic_auth(header: Optional[str], password: str) -> bool:
    """HTTP Basic: any user name, the configured password. Compared in constant time."""
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, given = decoded.partition(":")
    return hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8"))


def serve(runtime, stop: Callable[[], bool] = lambda: False, on_loop: Callable[[], None] = lambda: None) -> None:
    cfg = runtime.config.dashboard
    password = os.environ.get(cfg.password_env, "").strip() if cfg.password_env else ""
    if not password and not is_loopback(cfg.host):
        raise SystemExit(f"dashboard.host is {cfg.host!r}, which other machines can reach: set {cfg.password_env} in .env "
                         "(the page has no other protection), or bind to 127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not password:
                return True
            return check_basic_auth(self.headers.get("Authorization"), password)

        def do_GET(self) -> None:  # noqa: N802
            try:
                path = self.path.split("?", 1)[0]
                if path != "/healthz" and not self._authorized():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="scraper", charset="UTF-8"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/":
                    self._send(200, render_html(current_status(runtime)).encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._send(200, json.dumps(current_status(runtime), default=str).encode("utf-8"), "application/json")
                elif path == "/healthz":
                    status = current_status(runtime)
                    watchdog = any(r["role"] == "watchdog" for r in status["roles"])  # who will tell a human?
                    self._send(200 if status["ok"] else 503,
                               json.dumps({"ok": status["ok"], "problems": len(status["problems"]), "watchdog": watchdog}).encode(),
                               "application/json")
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception:  # noqa: BLE001
                log.exception("dashboard_request_failed")
                self._send(500, b"error", "text/plain")

        def log_message(self, *args) -> None:  # keep access lines out of the JSON log
            return

    # Always answer on localhost too, so the local health check works whatever address the page is on.
    hosts = [cfg.host] if is_loopback(cfg.host) or cfg.host == "0.0.0.0" else [cfg.host, "127.0.0.1"]
    servers = [ThreadingHTTPServer((host, cfg.port), Handler) for host in hosts]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("dashboard_ready", extra={"urls": [f"http://{h}:{cfg.port}/" for h in hosts], "password": bool(password)})
    try:
        while not stop():
            on_loop()
            time.sleep(2)
    finally:
        for server in servers:
            server.shutdown()
