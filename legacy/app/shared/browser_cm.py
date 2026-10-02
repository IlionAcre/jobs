# app/shared/browser_cm.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING, Generator

# Playwright & Camoufox
from playwright.sync_api import Page
from camoufox.sync_api import Camoufox

from contextlib import contextmanager

if TYPE_CHECKING:
    from app.config import SeleniumConfig, WaitsConfig, CamufouxConfig


@dataclass(frozen=True, slots=True)
class FetchOptions:
    wait_after_open_s: float = 2.0
    wait_for_css: Optional[str] = None
    wait_timeout_s: float = 30.0
    pause_for_manual_solve: bool = True
    post_enter_wait_s: float = 1.0


@contextmanager
def camoufox_session(cfg: "SeleniumConfig", camufoux_cfg: Optional["CamufouxConfig"] = None) -> Generator[Page, None, None]:
    """
    Create a Camoufox session (Playwright), utilizing config.
    Yields a Playwright Page object.
    """
    # Camoufox/Playwright options
    cf_kwargs = {
        "headless": (not cfg.headed),
        "geoip": True,
    }

    # Apply Camufoux overrides
    if camufoux_cfg and camufoux_cfg.active:
        logging.info("Using Camoufox configuration")
        if camufoux_cfg.headed is not None:
            cf_kwargs["headless"] = (not camufoux_cfg.headed)
        
        # New stealth options
        if camufoux_cfg.humanize_cursor is not None:
            cf_kwargs["humanize"] = camufoux_cfg.humanize_cursor
        if camufoux_cfg.geoip is not None:
            cf_kwargs["geoip"] = camufoux_cfg.geoip
        if camufoux_cfg.window_size is not None:
             cf_kwargs["window"] = tuple(camufoux_cfg.window_size)
        if camufoux_cfg.fonts is not None:
            cf_kwargs["fonts"] = camufoux_cfg.fonts
        if camufoux_cfg.addons is not None:
            cf_kwargs["addons"] = camufoux_cfg.addons
        # exclude_switches is not a valid Camoufox/Firefox argument, ignoring
        # if camufoux_cfg.exclude_switches is not None:
        #    cf_kwargs["exclude_switches"] = camufoux_cfg.exclude_switches

    with Camoufox(**cf_kwargs) as browser:
        page = browser.new_page()
        try:
            yield page
        finally:
            page.close()


def fetch_html(page: Page, url: str, opts: FetchOptions) -> str:
    page.goto(url)

    if opts.wait_after_open_s and opts.wait_after_open_s > 0:
        page.wait_for_timeout(opts.wait_after_open_s * 1000)

    if opts.wait_for_css:
        try:
            # wait_timeout_s is in seconds, Playwright takes ms
            page.wait_for_selector(opts.wait_for_css, timeout=opts.wait_timeout_s * 1000)
        except Exception as e:
            logging.warning(f"Wait for css '{opts.wait_for_css}' failed: {e}")

    if opts.pause_for_manual_solve:
        print("If you need to login / solve a challenge, do it now in the browser window.")
        input("Press ENTER to continue...")

        if opts.post_enter_wait_s and opts.post_enter_wait_s > 0:
             page.wait_for_timeout(opts.post_enter_wait_s * 1000)

    return page.content()


def fetch_html_with_waits(page: Page, url: str, waits: "WaitsConfig") -> str:
    wait_for_css = waits.job_tile_css if waits.wait_for_job_tiles else None
    opts = FetchOptions(
        wait_after_open_s=waits.wait_after_open_s,
        wait_for_css=wait_for_css,
        wait_timeout_s=waits.wait_timeout_s,
        pause_for_manual_solve=waits.pause_for_manual_solve,
        post_enter_wait_s=waits.post_enter_wait_s,
    )
    return fetch_html(page, url, opts)
