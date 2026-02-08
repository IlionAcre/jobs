# models.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# ============================================================
# DOMAIN MODELS
# ============================================================

_WS_RE = re.compile(r"\s+")


def clean_text(s: Optional[str]) -> Optional[str]:
    """Normalize whitespace and strip. Returns None for empty-ish values."""
    if s is None:
        return None
    s2 = _WS_RE.sub(" ", str(s)).strip()
    return s2 or None


@dataclass(frozen=True, slots=True)
class JobTile:
    """
    Minimal representation of a single Upwork search result tile
    (<article data-test="JobTile"> ... </article>).
    """

    title: Optional[str]
    url: Optional[str]
    snippet: Optional[str]
    budget: Optional[str]
    hourly_range: Optional[str]
    posted: Optional[str]
    location: Optional[str]
    job_type: Optional[str]
    experience_level: Optional[str]
    duration: Optional[str]
    tags: tuple[str, ...]

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "JobTile":
        """
        Convert a raw dict extracted by the parser into a strongly-typed JobTile.
        The parser can be loose; this method normalizes fields.
        """
        tags_val = raw.get("tags") or ()
        if isinstance(tags_val, str):
            tags = (tags_val,)
        else:
            tags = tuple(str(x) for x in tags_val)

        return JobTile(
            title=clean_text(raw.get("title")),
            url=clean_text(raw.get("url")),
            snippet=clean_text(raw.get("snippet")),
            budget=clean_text(raw.get("budget")),
            hourly_range=clean_text(raw.get("hourly_range")),
            posted=clean_text(raw.get("posted")),
            location=clean_text(raw.get("location")),
            job_type=clean_text(raw.get("job_type")),
            experience_level=clean_text(raw.get("experience_level")),
            duration=clean_text(raw.get("duration")),
            tags=tags,
        )


# ============================================================
# CONFIG MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class UpworkConfig:
    base_search_url: str
    quote_terms: bool = False


@dataclass(frozen=True, slots=True)
class SeleniumConfig:
    headed: bool = True
    incognito: bool = True
    uc_mode: bool = True
    start_maximized: bool = True


@dataclass(frozen=True, slots=True)
class CamufouxConfig:
    active: bool = False
    browser_name: Optional[str] = None
    # Selenium overrides
    headed: Optional[bool] = None
    incognito: Optional[bool] = None
    uc_mode: Optional[bool] = None
    start_maximized: Optional[bool] = None
    # Waits overrides
    wait_after_open_s: Optional[float] = None
    wait_for_job_tiles: Optional[bool] = None
    job_tile_css: Optional[str] = None
    wait_timeout_s: Optional[float] = None
    pause_for_manual_solve: Optional[bool] = None
    post_enter_wait_s: Optional[float] = None
    # Stealth options
    humanize_cursor: Optional[bool] = None
    geoip: Optional[bool] = None
    window_size: Optional[list[int]] = None
    fonts: Optional[list[str]] = None
    addons: Optional[list[str]] = None
    exclude_switches: Optional[list[str]] = None


@dataclass(frozen=True, slots=True)
class WaitsConfig:
    wait_after_open_s: float = 2.0
    wait_for_job_tiles: bool = True
    job_tile_css: str = 'article[data-test="JobTile"]'
    wait_timeout_s: float = 30.0
    pause_for_manual_solve: bool = True
    post_enter_wait_s: float = 1.0


@dataclass(frozen=True, slots=True)
class AppConfig:
    upwork: UpworkConfig
    selenium: SeleniumConfig
    waits: WaitsConfig
    camufoux: Optional[CamufouxConfig] = None


# ============================================================
# CONFIG LOADING
# ============================================================

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "config.yaml"
)


def _require_dict(x: Any, where: str) -> Dict[str, Any]:
    if not isinstance(x, dict):
        raise ValueError(f"Expected dict at '{where}', got {type(x).__name__}")
    return x


def _as_bool(x: Any, default: bool, where: str) -> bool:
    if x is None:
        return default
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        v = x.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"Invalid boolean at '{where}': {x!r}")


def _as_float(x: Any, default: float, where: str) -> float:
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.strip())
        except ValueError as e:
            raise ValueError(f"Invalid float at '{where}': {x!r}") from e
    raise ValueError(f"Invalid float at '{where}': {x!r}")


def _as_str(x: Any, default: str, where: str) -> str:
    if x is None:
        return default
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return default
        return s
    return str(x)


def _as_list(x: Any, default: Optional[list], where: str) -> Optional[list]:
    if x is None:
        return default
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        # Allow comma-separated strings
        return [s.strip() for s in x.split(",") if s.strip()]
    raise ValueError(f"Expected list at '{where}', got {type(x).__name__}")


def load_config(path: Optional[Path] = None) -> AppConfig:
    """
    Load YAML config into strongly-typed AppConfig.

    Default path:
      app/config/config.yaml

    Raises:
      FileNotFoundError if config doesn't exist
      ValueError for schema/type errors
    """
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    root = _require_dict(data, "root")

    # ---- upwork ----
    upwork_raw = _require_dict(root.get("upwork", {}), "upwork")
    upwork = UpworkConfig(
        base_search_url=_as_str(
            upwork_raw.get("base_search_url"),
            "https://www.upwork.com/nx/search/jobs/",
            "upwork.base_search_url",
        ),
        quote_terms=_as_bool(
            upwork_raw.get("quote_terms"),
            False,
            "upwork.quote_terms",
        ),
    )

    # ---- selenium ----
    selenium_raw = _require_dict(root.get("selenium", {}), "selenium")
    selenium = SeleniumConfig(
        headed=_as_bool(selenium_raw.get("headed"), True, "selenium.headed"),
        incognito=_as_bool(
            selenium_raw.get("incognito"), True, "selenium.incognito"
        ),
        uc_mode=_as_bool(selenium_raw.get("uc_mode"), True, "selenium.uc_mode"),
        start_maximized=_as_bool(
            selenium_raw.get("start_maximized"),
            True,
            "selenium.start_maximized",
        ),
    )

    # ---- camufoux (optional) ----
    camufoux_raw = root.get("camufoux")
    camufoux: Optional[CamufouxConfig] = None
    if camufoux_raw is not None:
        c_raw = _require_dict(camufoux_raw, "camufoux")
        camufoux = CamufouxConfig(
            active=_as_bool(c_raw.get("active"), False, "camufoux.active"),
            browser_name=_as_str(c_raw.get("browser_name"), "", "camufoux.browser_name") or None,
            headed=_as_bool(c_raw.get("headed"), None, "camufoux.headed"),
            incognito=_as_bool(c_raw.get("incognito"), None, "camufoux.incognito"),
            uc_mode=_as_bool(c_raw.get("uc_mode"), None, "camufoux.uc_mode"),
            start_maximized=_as_bool(c_raw.get("start_maximized"), None, "camufoux.start_maximized"),
            wait_after_open_s=_as_float(c_raw.get("wait_after_open_s"), None, "camufoux.wait_after_open_s"),
            wait_for_job_tiles=_as_bool(c_raw.get("wait_for_job_tiles"), None, "camufoux.wait_for_job_tiles"),
            job_tile_css=_as_str(c_raw.get("job_tile_css"), None, "camufoux.job_tile_css"),
            wait_timeout_s=_as_float(c_raw.get("wait_timeout_s"), None, "camufoux.wait_timeout_s"),
            pause_for_manual_solve=_as_bool(c_raw.get("pause_for_manual_solve"), None, "camufoux.pause_for_manual_solve"),
            post_enter_wait_s=_as_float(c_raw.get("post_enter_wait_s"), None, "camufoux.post_enter_wait_s"),
            # Stealth options
            humanize_cursor=_as_bool(c_raw.get("humanize_cursor"), None, "camufoux.humanize_cursor"),
            geoip=_as_bool(c_raw.get("geoip"), None, "camufoux.geoip"),
            window_size=_as_list(c_raw.get("window_size"), None, "camufoux.window_size"),
            fonts=_as_list(c_raw.get("fonts"), None, "camufoux.fonts"),
            addons=_as_list(c_raw.get("addons"), None, "camufoux.addons"),
            exclude_switches=_as_list(c_raw.get("exclude_switches"), None, "camufoux.exclude_switches"),
        )

    # ---- waits ----
    waits_raw = _require_dict(root.get("waits", {}), "waits")
    waits = WaitsConfig(
        wait_after_open_s=_as_float(
            waits_raw.get("wait_after_open_s"),
            2.0,
            "waits.wait_after_open_s",
        ),
        wait_for_job_tiles=_as_bool(
            waits_raw.get("wait_for_job_tiles"),
            True,
            "waits.wait_for_job_tiles",
        ),
        job_tile_css=_as_str(
            waits_raw.get("job_tile_css"),
            'article[data-test="JobTile"]',
            "waits.job_tile_css",
        ),
        wait_timeout_s=_as_float(
            waits_raw.get("wait_timeout_s"),
            30.0,
            "waits.wait_timeout_s",
        ),
        pause_for_manual_solve=_as_bool(
            waits_raw.get("pause_for_manual_solve"),
            True,
            "waits.pause_for_manual_solve",
        ),
        post_enter_wait_s=_as_float(
            waits_raw.get("post_enter_wait_s"),
            1.0,
            "waits.post_enter_wait_s",
        ),
    )

    return AppConfig(upwork=upwork, selenium=selenium, waits=waits, camufoux=camufoux)


__all__ = [
    "JobTile",
    "AppConfig",
    "UpworkConfig",
    "SeleniumConfig",
    "CamufouxConfig",
    "WaitsConfig",
    "load_config",
    "clean_text",
]

