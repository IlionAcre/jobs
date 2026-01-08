# app/shared/browser.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from seleniumbase import SB

if TYPE_CHECKING:
    from app.shared.models import SeleniumConfig, WaitsConfig


@dataclass(frozen=True, slots=True)
class FetchOptions:
    wait_after_open_s: float = 2.0
    wait_for_css: Optional[str] = None
    wait_timeout_s: float = 30.0
    pause_for_manual_solve: bool = True
    post_enter_wait_s: float = 1.0


def sb_session(cfg: "SeleniumConfig") -> SB:
    return SB(
        headed=cfg.headed,
        incognito=cfg.incognito,
        uc=cfg.uc_mode,
    )


def fetch_html(sb: SB, url: str, opts: FetchOptions) -> str:
    sb.open(url)

    if opts.wait_after_open_s and opts.wait_after_open_s > 0:
        sb.sleep(opts.wait_after_open_s)

    if opts.wait_for_css:
        sb.wait_for_element_present(opts.wait_for_css, timeout=opts.wait_timeout_s)

    if opts.pause_for_manual_solve:
        input("If you need to login / solve a challenge, do it now. Press ENTER to continue...")

        if opts.post_enter_wait_s and opts.post_enter_wait_s > 0:
            sb.sleep(opts.post_enter_wait_s)

    return sb.get_page_source()


def fetch_html_with_waits(sb: SB, url: str, waits: "WaitsConfig") -> str:
    wait_for_css = waits.job_tile_css if waits.wait_for_job_tiles else None
    opts = FetchOptions(
        wait_after_open_s=waits.wait_after_open_s,
        wait_for_css=wait_for_css,
        wait_timeout_s=waits.wait_timeout_s,
        pause_for_manual_solve=waits.pause_for_manual_solve,
        post_enter_wait_s=waits.post_enter_wait_s,
    )
    return fetch_html(sb, url, opts)
