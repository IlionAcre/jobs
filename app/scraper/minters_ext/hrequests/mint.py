"""
Mint a search token with hrequests (Go tls-client engine, Firefox or Chrome fingerprint).

Runs as a subprocess of the scraper in its own venv (requirements.txt here): hrequests pulls in gevent and
pins libraries the main environment uses at other versions. Only its Firefox profile gets through
(research/upwork_recon/10: firefox 16/16, chrome 0/3).

Usage: mint.py [--url URL] [firefox|chrome]
Prints one line: {"result": {"ok": bool, "token": str|null, "status": int}}
"""
from __future__ import annotations

import json
import sys

DEFAULT_URL = "https://www.upwork.com/nx/search/jobs/?nav_dir=pop&q=python&sort=recency"
COOKIE = "UniversalSearchNuxt_vt"


def main() -> None:
    args = sys.argv[1:]
    url = args[args.index("--url") + 1] if "--url" in args else DEFAULT_URL
    browser = "chrome" if "chrome" in args else "firefox"
    out = {"ok": False, "token": None}
    try:
        import hrequests

        session = hrequests.Session(browser=browser, os="win")
        try:
            resp = session.get(url, timeout=30)
            out["status"] = resp.status_code
            token = None
            for cookie in session.cookies:
                if cookie.name == COOKIE:
                    token = cookie.value
            if resp.status_code == 200 and token and token.startswith("oauth2v2_"):
                out.update(ok=True, token=token)
        finally:
            session.close()
    except Exception as ex:  # noqa: BLE001
        out["error"] = repr(ex)[:300]
    print(json.dumps({"result": out}), flush=True)


if __name__ == "__main__":
    main()
