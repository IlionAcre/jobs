"""
Stealth launch recipe + Cloudflare Turnstile solver for patchright (patched Playwright/Chromium).

Adapted from Scrapling 0.4.15 (https://github.com/D4Vinci/Scrapling), BSD 3-Clause,
Copyright (c) 2024, Karim shoair — see LICENSE.scrapling next to this file.
Sources: scrapling/engines/constants.py, engines/_browsers/_base.py (StealthySessionMixin),
engines/_browsers/_stealth.py (StealthySession._cloudflare_solver), engines/toolbelt/fingerprints.py.

What was kept: the Chromium flags, the persistent-context options, the headless user-agent fix, the
challenge detection, and the "click the Turnstile box at a fixed offset" solver.
What was dropped: page pool, proxy rotation, response/selector objects, header generation (browserforge),
validation models, async variant. Only dependency: patchright.
"""
from __future__ import annotations

import json
import re
from importlib.util import find_spec
from pathlib import Path
from random import randint
from typing import Any, Callable, Dict, Optional, Tuple

# --- scrapling/engines/constants.py (verbatim) -------------------------------------------------

HARMFUL_ARGS = (
    # Ignored to avoid detection and the popup crashing bug abuse: https://issues.chromium.org/issues/340836884
    "--enable-automation",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
)

DEFAULT_ARGS = (
    "--no-pings",
    "--no-first-run",
    "--disable-infobars",
    "--disable-breakpad",
    "--no-service-autorun",
    "--homepage=about:blank",
    "--password-store=basic",
    "--disable-hang-monitor",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-search-engine-choice-screen",
)

STEALTH_ARGS = (
    "--test-type",
    "--mute-audio",
    "--disable-sync",
    "--hide-scrollbars",
    "--disable-logging",
    "--start-maximized",  # For headless check bypass
    "--enable-async-dns",
    "--use-mock-keychain",
    "--disable-translate",
    "--disable-voice-input",
    "--window-position=0,0",
    "--disable-wake-on-wifi",
    "--ignore-gpu-blocklist",
    "--enable-tcp-fast-open",
    "--enable-web-bluetooth",
    "--disable-cloud-import",
    "--disable-print-preview",
    "--disable-dev-shm-usage",
    "--metrics-recording-only",
    "--disable-crash-reporter",
    "--disable-partial-raster",
    "--disable-gesture-typing",
    "--disable-checker-imaging",
    "--disable-prompt-on-repost",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--aggressive-cache-discard",
    "--disable-cookie-encryption",
    "--disable-domain-reliability",
    "--disable-threaded-animation",
    "--disable-threaded-scrolling",
    "--enable-simple-cache-backend",
    "--disable-background-networking",
    "--enable-surface-synchronization",
    "--disable-image-animation-resync",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--prerender-from-omnibox=disabled",
    "--safebrowsing-disable-auto-update",
    "--disable-offer-upload-credit-cards",
    "--disable-background-timer-throttling",
    "--disable-new-content-rendering-timeout",
    "--run-all-compositor-stages-before-draw",
    "--disable-client-side-phishing-detection",
    "--disable-backgrounding-occluded-windows",
    "--disable-layer-tree-host-memory-pressure",
    "--autoplay-policy=user-gesture-required",
    "--disable-offer-store-unmasked-wallet-cards",
    "--disable-blink-features=AutomationControlled",
    "--disable-component-extensions-with-background-pages",
    "--enable-features=NetworkService,NetworkServiceInProcess,TrustTokens,TrustTokensAlwaysAllowIssuance",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,BlinkGenPropertyTrees",
)

# Resource types Scrapling drops with disable_resources=True
EXTRA_RESOURCES = {
    "font", "image", "media", "beacon", "object", "imageset", "texttrack", "websocket", "csp_report", "stylesheet",
}

# --- StealthySessionMixin.__validate__ / __validate_routine__ (context options, verbatim values) ----

CONTEXT_OPTIONS: Dict[str, Any] = {
    "color_scheme": "dark",  # bypasses the 'prefersLightColor' check in creepjs
    "device_scale_factor": 2,
    "is_mobile": False,
    "has_touch": False,
    "service_workers": "allow",
    "ignore_https_errors": True,
    "screen": {"width": 1920, "height": 1080},
    "viewport": {"width": 1920, "height": 1080},
    "permissions": ["geolocation", "notifications"],
}


def driven_chromium_version() -> Optional[int]:
    """Chromium major version patchright drives (from its bundled browsers.json). From fingerprints.py."""
    spec = find_spec("patchright")
    if spec is None or not spec.origin:
        return None
    path = Path(spec.origin).parent / "driver" / "package" / "browsers.json"
    try:
        browsers = json.loads(path.read_bytes()).get("browsers", [])
        version = next((b["browserVersion"] for b in browsers if b.get("name") == "chromium"), None)
        return int(version.partition(".")[0]) if version else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def headless_user_agent() -> str:
    """Headless Chromium announces 'HeadlessChrome'; Scrapling overrides it with a normal Chrome UA of the
    driven version. (Scrapling builds it with browserforge; a fixed Windows template is enough for us.)"""
    major = driven_chromium_version() or 153
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


def launch_options(*, headless: bool = True, channel: str = "chromium",
                   extra_flags: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """kwargs for `chromium.launch_persistent_context(user_data_dir=..., **launch_options())`."""
    opts: Dict[str, Any] = {
        "args": list(DEFAULT_ARGS) + list(STEALTH_ARGS) + list(extra_flags),
        "ignore_default_args": list(HARMFUL_ARGS),
        "headless": headless,
        "channel": channel,
        **CONTEXT_OPTIONS,
    }
    if headless:
        opts["user_agent"] = headless_user_agent()
    return opts


# --- Cloudflare detection + solver (StealthySessionMixin._detect_cloudflare, StealthySession._cloudflare_solver) ---

CF_PATTERN = re.compile(r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*")
_EMBEDDED_RE = re.compile(r"<script[^>]+src=[\"'][^\"']*challenges\.cloudflare\.com/turnstile/v", re.I)


def page_content(page) -> str:
    """page.content() throws while a navigation is in flight; that just means 'ask again'."""
    try:
        return page.content()
    except Exception:  # noqa: BLE001
        return ""


def detect_cloudflare(content: str) -> Optional[str]:
    """Return 'non-interactive' | 'managed' | 'interactive' | 'embedded' | None."""
    for ctype in ("non-interactive", "managed", "interactive"):
        if f"cType: '{ctype}'" in content:
            return ctype
    if _EMBEDDED_RE.search(content):
        return "embedded"
    return None


def click_turnstile(page, challenge_type: str, done: Callable[[], bool]) -> bool:
    """One solve attempt: locate the Turnstile box and click it. Returns True if a click was sent.

    Same geometry as Scrapling (iframe bounding box + ~27px/26px), but non-recursive and interruptible:
    `done()` is checked while waiting so the caller can stop as soon as it has what it came for.
    """
    box_selector = "#cf_turnstile div, #cf-turnstile div, .turnstile>div>div"
    if challenge_type != "embedded":
        box_selector = ".main-content p+div>div>div"
        for _ in range(20):  # wait for the verify spinner to become the widget iframe, or to pass on its own
            if done() or page.frame(url=CF_PATTERN) is not None or detect_cloudflare(page_content(page)) is None:
                break
            page.wait_for_timeout(500)
    if done():
        return False

    outer_box: Any = None
    iframe = page.frame(url=CF_PATTERN)
    if iframe is not None:
        try:
            iframe.wait_for_load_state("load", timeout=5000)
            element = iframe.frame_element()
            for _ in range(20):
                if challenge_type == "embedded" or element.is_visible() or done():
                    break
                page.wait_for_timeout(500)
            outer_box = element.bounding_box()
        except Exception:  # noqa: BLE001  (frame detached = challenge moved on)
            outer_box = None

    if not outer_box:
        if done() or detect_cloudflare(page_content(page)) is None:
            return False
        try:
            outer_box = page.locator(box_selector).last.bounding_box(timeout=2000)
        except Exception:  # noqa: BLE001
            outer_box = None
        if not outer_box:
            return False

    x, y = outer_box["x"] + randint(26, 28), outer_box["y"] + randint(25, 27)
    page.mouse.click(x, y, delay=randint(100, 200), button="left")
    return True
