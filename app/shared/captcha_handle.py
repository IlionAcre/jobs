# app/shared/captcha_handle.py
"""
Cloudflare Turnstile captcha handler for SeleniumBase.
Used by monitor_uw.py (SeleniumBase/undetected-chromedriver).
"""
from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from seleniumbase import SB

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_CHALLENGE_TITLE = "just a moment"


def is_challenge_page(sb: "SB") -> bool:
    """Return True if the current page is a Cloudflare challenge/interstitial."""
    try:
        title = sb.get_title().lower()
        if _CHALLENGE_TITLE in title:
            return True
    except Exception:
        pass

    # Fallback: look for the hidden turnstile response input
    try:
        return sb.is_element_present('input[name="cf-turnstile-response"]')
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Solving
# ---------------------------------------------------------------------------

# Selectors tried *inside* the Turnstile iframe (order matters – most
# specific first so we don't accidentally click the wrong thing).
_IFRAME_CHECKBOX_SELECTORS = [
    "input[type='checkbox']",
    ".cb-lb",
    ".cb-c",
    "#challenge-stage input[type='checkbox']",
    "body",  # last resort – the whole widget is the click target
]

# Patterns that identify the Turnstile iframe's src attribute.
_TURNSTILE_IFRAME_PATTERNS = [
    "challenges.cloudflare.com",
    "turnstile",
    "challenge-platform",
]


def _find_turnstile_iframe(sb: "SB") -> str | None:
    """Return the CSS selector for the Turnstile iframe, or None."""
    try:
        iframes = sb.find_elements("iframe")
    except Exception:
        return None

    for idx, iframe in enumerate(iframes):
        try:
            src = iframe.get_attribute("src") or ""
            if any(pat in src for pat in _TURNSTILE_IFRAME_PATTERNS):
                # Build a unique selector we can pass to switch_to_frame
                iframe_id = iframe.get_attribute("id")
                if iframe_id:
                    return f"iframe#{iframe_id}"
                iframe_name = iframe.get_attribute("name")
                if iframe_name:
                    return f"iframe[name='{iframe_name}']"
                # Fallback: nth iframe
                return f"iframe:nth-of-type({idx + 1})"
        except Exception:
            continue
    return None


def _human_click(sb: "SB", selector: str) -> bool:
    """Click an element with a small random delay to look more human."""
    try:
        if not sb.is_element_visible(selector):
            return False
        # Small pre-click pause
        time.sleep(random.uniform(0.3, 0.8))
        sb.slow_click(selector)
        return True
    except Exception as e:
        log.debug("click failed on %s: %s", selector, e)
        return False


def solve_captcha(sb: "SB", *, max_attempts: int = 3) -> bool:
    """
    Detect and attempt to solve a Cloudflare Turnstile challenge.

    Returns True if a challenge was detected (regardless of whether the
    solve succeeded – the caller should re-check readiness).
    """
    if not is_challenge_page(sb):
        return False

    log.info("[captcha-sb] Cloudflare challenge detected")

    for attempt in range(1, max_attempts + 1):
        log.info("[captcha-sb] Solve attempt %d/%d", attempt, max_attempts)

        # 1. Try clicking the widget directly on the main page first
        for sel in _IFRAME_CHECKBOX_SELECTORS[:-1]:  # skip "body" for main page
            if _human_click(sb, sel):
                log.info("[captcha-sb] Clicked %s on main page", sel)
                time.sleep(random.uniform(2.0, 4.0))
                if not is_challenge_page(sb):
                    log.info("[captcha-sb] Challenge solved!")
                    return True

        # 2. Find and switch into the Turnstile iframe
        iframe_sel = _find_turnstile_iframe(sb)
        if iframe_sel:
            try:
                sb.switch_to_frame(iframe_sel)
                log.info("[captcha-sb] Switched into iframe: %s", iframe_sel)

                for sel in _IFRAME_CHECKBOX_SELECTORS:
                    if _human_click(sb, sel):
                        log.info("[captcha-sb] Clicked %s inside iframe", sel)
                        break

                sb.switch_to_default_content()
                time.sleep(random.uniform(3.0, 5.0))

                if not is_challenge_page(sb):
                    log.info("[captcha-sb] Challenge solved!")
                    return True

            except Exception as e:
                log.warning("[captcha-sb] iframe interaction failed: %s", e)
                try:
                    sb.switch_to_default_content()
                except Exception:
                    pass

        # Brief pause before retry
        time.sleep(random.uniform(1.0, 2.0))

    log.warning("[captcha-sb] Could not solve challenge after %d attempts", max_attempts)
    return True  # challenge was detected, even if not solved
