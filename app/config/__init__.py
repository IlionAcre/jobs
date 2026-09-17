from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


# -------------------------
# Pydantic config models
# -------------------------

class UpworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_search_url: str = Field(default="https://www.upwork.com/nx/search/jobs/")
    quote_terms: bool = Field(default=False)
    show_description: bool = Field(default=True)


class SeleniumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headed: bool = True
    incognito: bool = True
    uc_mode: bool = True
    start_maximized: bool = True


class WaitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wait_after_open_s: float = 2.0
    wait_for_job_tiles: bool = True
    job_tile_css: str = 'article[data-test="JobTile"]'
    wait_timeout_s: float = 30.0
    pause_for_manual_solve: bool = True
    post_enter_wait_s: float = 1.0


class CamufouxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool = False
    browser_name: Optional[str] = None
    headed: Optional[bool] = None
    incognito: Optional[bool] = None
    uc_mode: Optional[bool] = None
    start_maximized: Optional[bool] = None
    wait_after_open_s: Optional[float] = None
    wait_for_job_tiles: Optional[bool] = None
    job_tile_css: Optional[str] = None
    wait_timeout_s: Optional[float] = None
    pause_for_manual_solve: Optional[bool] = None
    post_enter_wait_s: Optional[float] = None
    humanize_cursor: Optional[bool] = None
    geoip: Optional[bool] = None
    window_size: Optional[list[int]] = None
    fonts: Optional[list[str]] = None
    addons: Optional[list[str]] = None
    exclude_switches: Optional[list[str]] = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upwork: UpworkConfig = Field(default_factory=UpworkConfig)
    selenium: SeleniumConfig = Field(default_factory=SeleniumConfig)
    waits: WaitsConfig = Field(default_factory=WaitsConfig)
    camufoux: Optional[CamufouxConfig] = None


# -------------------------
# Loader
# -------------------------

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_ENV_CONFIG_PATH_KEY = "APP_CONFIG_PATH"

_cached_config: Optional[AppConfig] = None


def load_config(path: str | Path | None = None, *, cache: bool = True) -> AppConfig:
    """
    Load and validate YAML config.

    Resolution order:
      1) explicit `path` argument
      2) env var APP_CONFIG_PATH
      3) app/config/config.yaml (package default)

    If cache=True, subsequent calls reuse the loaded config.
    """
    global _cached_config
    if cache and _cached_config is not None:
        return _cached_config

    if path is None:
        env_path = os.getenv(_ENV_CONFIG_PATH_KEY)
        path = Path(env_path) if env_path else _DEFAULT_CONFIG_PATH
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")

    loaded: Dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = loaded.get("root", loaded) if isinstance(loaded, dict) else {}
    cfg = AppConfig.model_validate(raw)

    if cache:
        _cached_config = cfg
    return cfg


__all__ = [
    "AppConfig",
    "UpworkConfig",
    "SeleniumConfig",
    "WaitsConfig",
    "CamufouxConfig",
    "load_config",
]
