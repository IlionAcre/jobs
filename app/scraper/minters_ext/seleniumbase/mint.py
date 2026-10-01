"""
Last-resort minter: SeleniumBase UC/CDP mode with the project's Turnstile click handler.

Slow (~35-45 s) and heavy (~1.2 GB), but it is the only tool we have seen pass a real Cloudflare challenge
(8 of 10 runs, research/upwork_recon/10), so it sits at the end of the chain. Runs in the main environment.

Usage: mint.py [--url URL] [--headless]
Prints one line: {"result": {"ok": bool, "token": str|null, "challenge_seen": bool, ...}}
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from seleniumbase import SB  # noqa: E402

from app.shared.captcha_handle import is_challenge_page, solve_captcha  # noqa: E402

DEFAULT_URL = "https://www.upwork.com/nx/search/jobs/?nav_dir=pop&q=python&sort=recency"
TOKEN_RE = re.compile(r"UniversalSearchNuxt_vt=(oauth2v2_\w+)")
TIMEOUT_S = 75


def _cookie_string(sb) -> str:
    # In UC mode sb.open() switches to CDP, where execute_script is Runtime.evaluate: a bare expression,
    # no `return` (research/upwork_recon/08).
    return sb.cdp.evaluate("document.cookie") or ""


def main() -> None:
    args = sys.argv[1:]
    url = args[args.index("--url") + 1] if "--url" in args else DEFAULT_URL
    headless = "--headless" in args
    t0 = time.time()
    out = {"ok": False, "token": None, "challenge_seen": False, "solver_calls": 0}
    try:
        with SB(uc=True, headless=headless, headed=not headless, incognito=True) as sb:
            sb.activate_cdp_mode("about:blank")
            sb.open(url)
            last_solve = 0.0
            while time.time() - t0 < TIMEOUT_S:
                match = TOKEN_RE.search(_cookie_string(sb))
                if match:
                    out.update(ok=True, token=match.group(1))
                    break
                if is_challenge_page(sb):
                    out["challenge_seen"] = True
                    if time.time() - t0 > 12 and time.time() - last_solve > 10:
                        solve_captcha(sb)
                        out["solver_calls"] += 1
                        last_solve = time.time()
                time.sleep(1)
    except Exception as ex:  # noqa: BLE001
        out["error"] = repr(ex)[:300]
    out["seconds"] = round(time.time() - t0, 1)
    print(json.dumps({"result": out}), flush=True)


if __name__ == "__main__":
    main()
