# app/shared/captcha_handle_cm.py
"""
Cloudflare Turnstile captcha handler for Playwright / Camoufox.
Used by monitor_uw_cm.py.
"""
from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import Page, Locator, Frame

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_CHALLENGE_TITLE = "just a moment"

_TURNSTILE_FRAME_PATTERNS = [
    "challenges.cloudflare.com",
    "turnstile",
    "challenge-platform",
]


def is_challenge_page(page: Page) -> bool:
    """Return True if the current page is a Cloudflare challenge/interstitial."""
    try:
        title = page.title().lower()
        if _CHALLENGE_TITLE in title:
            return True
    except Exception:
        pass
    try:
        return page.locator('input[name="cf-turnstile-response"]').count() > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Human-like click helpers
# ---------------------------------------------------------------------------


def _geometry_click(locator: Locator) -> bool:
    """Click at a random point within the element (Gaussian towards centre)."""
    try:
        if not locator.is_visible(timeout=500):
            return False

        box = locator.bounding_box()
        if not box:
            return False

        w, h = box["width"], box["height"]
        tx = max(0, min(w, random.gauss(w / 2, w / 6)))
        ty = max(0, min(h, random.gauss(h / 2, h / 6)))

        locator.click(
            position={"x": tx, "y": ty},
            delay=random.uniform(80, 250),
        )
        return True
    except Exception as e:
        log.debug("geometry_click failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Frame helpers
# ---------------------------------------------------------------------------

_IFRAME_SELECTORS = [
    "input[type='checkbox']",
    ".cb-lb",
    ".cb-c",
    "#challenge-stage input[type='checkbox']",
]


def _find_turnstile_frame(page: Page) -> Frame | None:
    """Return the Turnstile iframe Frame object, or None."""
    for frame in page.frames:
        url = frame.url
        if any(pat in url for pat in _TURNSTILE_FRAME_PATTERNS):
            return frame
    return None


def _try_click_in_frame(frame: Frame) -> bool:
    """Try clicking known selectors inside a frame, falling back to body."""
    for sel in _IFRAME_SELECTORS:
        loc = frame.locator(sel).first
        try:
            if loc.is_visible(timeout=300):
                log.info("[captcha-cm] Found %s in frame, clicking", sel)
                time.sleep(random.uniform(0.3, 0.9))
                return _geometry_click(loc)
        except Exception:
            continue

    # Fallback: click the body of the widget frame (the whole area is the target)
    try:
        body = frame.locator("body")
        if body.is_visible(timeout=300):
            log.info("[captcha-cm] Falling back to body click in frame")
            time.sleep(random.uniform(0.3, 0.9))
            return _geometry_click(body)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def solve_captcha(page: Page, *, max_attempts: int = 3) -> bool:
    """
    Detect and attempt to solve a Cloudflare Turnstile challenge.

    Returns True if a challenge was detected (regardless of whether the
    solve succeeded – the caller should re-check readiness).
    """
    if not is_challenge_page(page):
        return False

    log.info("[captcha-cm] Cloudflare challenge detected")

    for attempt in range(1, max_attempts + 1):
        log.info("[captcha-cm] Solve attempt %d/%d", attempt, max_attempts)

        # 1. Try clicking selectors on the main page
        for sel in _IFRAME_SELECTORS:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=300):
                    log.info("[captcha-cm] Found %s on main page", sel)
                    time.sleep(random.uniform(0.3, 0.9))
                    _geometry_click(loc)
                    time.sleep(random.uniform(2.0, 4.0))
                    if not is_challenge_page(page):
                        log.info("[captcha-cm] Challenge solved!")
                        return True
            except Exception:
                continue

        # 2. Find and interact with the Turnstile iframe
        frame = _find_turnstile_frame(page)
        if frame:
            log.info("[captcha-cm] Found Turnstile iframe: %s", frame.url[:80])
            _try_click_in_frame(frame)
            time.sleep(random.uniform(3.0, 5.0))

            if not is_challenge_page(page):
                log.info("[captcha-cm] Challenge solved!")
                return True

        # Brief pause before retry
        time.sleep(random.uniform(1.0, 2.0))

    log.warning("[captcha-cm] Could not solve after %d attempts", max_attempts)

    # Save debug screenshot
    try:
        page.screenshot(path="debug_captcha_view.png")
    except Exception:
        pass

    return True  # challenge detected, even if not solved
