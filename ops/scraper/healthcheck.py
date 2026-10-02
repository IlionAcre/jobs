"""
Outside check: tells you on Telegram when the pipeline cannot tell you itself.

The watchdog reports problems while it is running. This script covers the rest: the whole pipeline is down,
the dashboard does not answer, or the watchdog role is missing. It is run every few minutes by the scheduler
of the operating system (task "UpworkScraperCheck" / upwork-scraper-check.timer), shares no process and no
code with the pipeline, and uses only the standard library, so it works even when the virtual environment,
Redis or Postgres are broken.

  python ops/scraper/healthcheck.py                  check once, alert if needed
  python ops/scraper/healthcheck.py --dry-run        print what it would do, send nothing

It alerts once after `--after` consecutive failed checks (default 2), and once more when things recover.
Reads TELEGRAM_BOT_TOKEN and ALERTS_CHAT_ID (else TELEGRAM_CHAT_ID) from the environment or the repo's .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "logs" / "healthcheck_state.json"


def read_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env reader (KEY=value, optional quotes, # comments)."""
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().removeprefix("export ").strip()] = value
    return values


def probe(url: str, timeout: float = 10.0) -> Tuple[bool, str]:
    """(healthy, reason). Healthy = the dashboard answers and the watchdog is running.

    A pipeline that answers 503 with its watchdog alive is NOT reported here: the watchdog is already
    telling the same person about that problem, in more detail.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as ex:  # 503 = up, with an open critical problem
        body = ex.read()
    except Exception as ex:  # noqa: BLE001  (refused, timeout, DNS...)
        return False, f"the pipeline does not answer at {url} ({type(ex).__name__})"
    try:
        data = json.loads(body)
    except ValueError:
        return False, f"unexpected answer from {url}"
    if not data.get("watchdog", False):
        return False, "the pipeline answers but its watchdog is not running, so problems would go unreported"
    return True, "ok"


def decide(state: Dict, healthy: bool, reason: str, *, after: int) -> Tuple[Dict, Optional[str]]:
    """Pure state machine: (new state, message to send or None)."""
    failures = int(state.get("failures", 0))
    alerted = bool(state.get("alerted", False))
    if healthy:
        message = "✅ Outside check: the scraper pipeline is answering again." if alerted else None
        return {"failures": 0, "alerted": False}, message
    failures += 1
    if failures >= after and not alerted:
        return ({"failures": failures, "alerted": True, "reason": reason},
                f"🚨 Outside check: {reason}.\nFailed {failures} checks in a row. Look at logs/scraper.log and logs/start_scraper.log.")
    return {"failures": failures, "alerted": alerted, "reason": reason}, None


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=timeout) as response:
        response.read()


def run(url: str, state_path: Path, *, after: int, send: Callable[[str], None], dry_run: bool = False) -> int:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    healthy, reason = probe(url)
    new_state, message = decide(state, healthy, reason, after=after)
    new_state["checked_at"] = datetime.now().isoformat(timespec="seconds")
    if dry_run:
        print(f"healthy={healthy} reason={reason!r} would_send={message!r}")
        return 0 if healthy else 1
    if message:
        try:
            send(message)
        except Exception as ex:  # noqa: BLE001  (keep "not alerted" so the next run tries again)
            print(f"could not send the alert: {type(ex).__name__}", file=sys.stderr)
            new_state["alerted"] = bool(state.get("alerted", False))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state), encoding="utf-8")
    return 0 if healthy else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8787/healthz")
    parser.add_argument("--after", type=int, default=2, help="alert after this many failed checks in a row")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = {**read_env_file(ROOT / ".env"), **os.environ}
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (env.get("ALERTS_CHAT_ID") or env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not args.dry_run and not (token and chat_id):
        print("TELEGRAM_BOT_TOKEN and ALERTS_CHAT_ID (or TELEGRAM_CHAT_ID) are required", file=sys.stderr)
        return 2
    return run(args.url, Path(args.state), after=args.after, dry_run=args.dry_run,
               send=lambda text: send_telegram(token, chat_id, text))


if __name__ == "__main__":
    sys.exit(main())
