"""
Lean token minter: open the Upwork search page in a stealth Chromium, pass Cloudflare Turnstile,
return the `UniversalSearchNuxt_vt` cookie (the search API's Bearer token), exit.

Runs as a subprocess of the scraper (app/scraper/tokens/minters.py::SubprocessMinter) in its own venv
(requirements.txt here): patchright needs a newer Playwright than the main environment.
Stealth recipe + solver: stealth.py (adapted from Scrapling, see LICENSE.scrapling).

Unlike Scrapling's fetch(), this stops the moment the cookie exists: the token arrives in the Set-Cookie of
the post-challenge HTML response, so Upwork's own page never needs to finish loading.

Usage: mint.py [--url URL] [--headed] [--block | --allowlist] [--shell | --chrome] [--lowmem] [--timeout N]
  --block      drop fonts/images/media/stylesheets etc. everywhere (Scrapling's disable_resources)
  --allowlist  only allow main documents, Cloudflare challenge traffic; abort everything else
  --shell      use chromium-headless-shell (smaller binary, old headless mode)
  --chrome     drive the installed Google Chrome instead of patchright's Chromium
  --lowmem     one renderer, no site isolation, 1280x720 @1x
Prints one line: {"result": {...}} (token included; callers must not log it).
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from patchright.sync_api import sync_playwright

from stealth import EXTRA_RESOURCES, click_turnstile, detect_cloudflare, launch_options, page_content

URL = "https://www.upwork.com/nx/search/jobs/?nav_dir=pop&q=python%20OR%20scraping&sort=recency"
COOKIE = "UniversalSearchNuxt_vt"
LOWMEM_FLAGS = ("--renderer-process-limit=1", "--disable-site-isolation-trials")


def _token(context) -> Optional[str]:
    for c in context.cookies("https://www.upwork.com"):
        if c["name"] == COOKIE and c["value"].startswith("oauth2v2_"):
            return c["value"]
    return None


def _block_types(route) -> None:
    if route.request.resource_type in EXTRA_RESOURCES:
        route.abort()
    else:
        route.continue_()


def _allowlist(route) -> None:
    req = route.request
    host = urlparse(req.url).hostname or ""
    if (
        host == "challenges.cloudflare.com"
        or "/cdn-cgi/" in req.url
        or (req.resource_type == "document" and host.endswith("upwork.com"))
    ):
        route.continue_()
    else:
        route.abort()


def mint(*, url: str = URL, headless: bool = True, block: bool = False, allowlist: bool = False, shell: bool = False,
         chrome: bool = False, lowmem: bool = False, timeout_s: float = 60.0) -> Dict[str, Any]:
    t0 = time.time()
    out: Dict[str, Any] = {"ok": False, "token": None, "clicks": 0, "challenge_types": [], "doc_statuses": []}
    opts = launch_options(
        headless=headless,
        channel="chromium-headless-shell" if shell else "chrome" if chrome else "chromium",
        extra_flags=LOWMEM_FLAGS if lowmem else (),
    )
    if lowmem:
        opts.update(device_scale_factor=1, screen={"width": 1280, "height": 720}, viewport={"width": 1280, "height": 720})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile, sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(user_data_dir=profile, **opts)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15_000)
            statuses: List[int] = out["doc_statuses"]
            page.on("response", lambda r: statuses.append(r.status)
                    if r.request.resource_type == "document" and r.request.frame == page.main_frame else None)
            if allowlist:
                page.route("**/*", _allowlist)
            elif block:
                page.route("**/*", _block_types)

            out["launch_s"] = round(time.time() - t0, 1)
            try:
                page.goto(url, referer="https://www.google.com/", wait_until="commit", timeout=30_000)
            except Exception as ex:  # noqa: BLE001
                out["goto_error"] = repr(ex)[:160]

            deadline = t0 + timeout_s
            last_click = 0.0
            token: Optional[str] = None
            while time.time() < deadline:
                token = _token(context)
                if token:
                    break
                ctype = detect_cloudflare(page_content(page))
                if ctype and ctype not in out["challenge_types"]:
                    out["challenge_types"].append(ctype)
                # 'non-interactive' clears by itself; the others may need the checkbox clicked
                if ctype in ("managed", "interactive", "embedded") and time.time() - last_click > 6:
                    if click_turnstile(page, ctype, done=lambda: _token(context) is not None):
                        out["clicks"] += 1
                    last_click = time.time()
                page.wait_for_timeout(250)

            if token:
                out.update(ok=True, token=token)
            else:
                try:
                    out["title"] = page.title()
                except Exception:  # noqa: BLE001
                    pass
            out["seconds"] = round(time.time() - t0, 1)
        finally:
            context.close()
    return out


def main() -> None:
    a = sys.argv[1:]
    timeout = float(a[a.index("--timeout") + 1]) if "--timeout" in a else 60.0
    try:
        url = a[a.index("--url") + 1] if "--url" in a else URL
        out = mint(url=url, headless="--headed" not in a, block="--block" in a, allowlist="--allowlist" in a,
                   shell="--shell" in a, chrome="--chrome" in a, lowmem="--lowmem" in a, timeout_s=timeout)
    except Exception as ex:  # noqa: BLE001
        out = {"ok": False, "token": None, "error": repr(ex)[:300]}
    print(json.dumps({"result": out}), flush=True)


if __name__ == "__main__":
    main()
