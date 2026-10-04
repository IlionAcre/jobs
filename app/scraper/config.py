"""
Typed, validated settings for the scraper pipeline (app/config/scraper.yaml).

Resolution order for the file: explicit path -> env SCRAPER_CONFIG_PATH -> app/config/scraper.yaml.
Unknown keys and dangling references (a minter naming a transport that doesn't exist) fail at load time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.scraper.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "app" / "config" / "scraper.yaml"
ENV_PATH_KEY = "SCRAPER_CONFIG_PATH"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UpworkConfig(_Model):
    search_page_url: str
    api_url: str
    token_cookie: str
    job_url_template: str


class TransportConfig(_Model):
    kind: Literal["curl_cffi", "httpcloak", "requests_h1"]
    impersonate: Optional[str] = None  # curl_cffi browser profile, e.g. "chrome150"
    preset: Optional[str] = None  # httpcloak preset, e.g. "chrome-latest"
    timeout_s: float = 30.0
    proxy: Optional[str] = None

    @model_validator(mode="after")
    def _required_per_kind(self) -> "TransportConfig":
        if self.kind == "curl_cffi" and not self.impersonate:
            raise ValueError("curl_cffi transport needs `impersonate`")
        if self.kind == "httpcloak" and not self.preset:
            raise ValueError("httpcloak transport needs `preset`")
        return self


class PollerConfig(_Model):
    transport: str
    fallback_transport: Optional[str] = None  # used if the API challenges the primary client
    interval_s: float = Field(gt=0)
    jitter: float = Field(ge=0, lt=1)
    ids_count: int = Field(ge=1, le=50)  # the API caps a page at 50
    details_count: int = Field(ge=1, le=50)
    max_job_age_minutes: float = Field(gt=0)


class TokensConfig(_Model):
    refresh_after_hours: float = Field(gt=0)
    max_age_hours: float = Field(gt=0)

    @model_validator(mode="after")
    def _refresh_before_max(self) -> "TokensConfig":
        if self.refresh_after_hours >= self.max_age_hours:
            raise ValueError("refresh_after_hours must be smaller than max_age_hours")
        return self


class MinterConfig(_Model):
    name: str
    kind: Literal["http", "subprocess"]
    transport: Optional[str] = None  # kind=http
    python: Optional[str] = None  # kind=subprocess: venv dir or interpreter, relative to the repo root
    script: Optional[str] = None  # kind=subprocess
    args: List[str] = Field(default_factory=list)
    timeout_s: float = 60.0
    attempts: int = Field(default=1, ge=1)
    min_free_mem_mb: Optional[int] = None

    @model_validator(mode="after")
    def _required_per_kind(self) -> "MinterConfig":
        if self.kind == "http" and not self.transport:
            raise ValueError(f"minter {self.name!r}: kind=http needs `transport`")
        if self.kind == "subprocess" and not (self.python and self.script):
            raise ValueError(f"minter {self.name!r}: kind=subprocess needs `python` and `script`")
        return self


class FailurePolicyConfig(_Model):
    transient_retries: int = Field(ge=0)
    transient_backoff_s: List[float] = Field(min_length=1)
    rate_limit_backoff_s: float = Field(gt=0)


class RateLimitConfig(_Model):
    api_per_minute: int = Field(gt=0)
    page_per_10min: int = Field(gt=0)


class DispatcherConfig(_Model):
    mode: Literal["shadow", "live"]
    admin_chat_id: Optional[int] = None
    # Add a line saying where an alert came from (your Upwork search, or the all-jobs collector). For
    # experiments that compare the two; a job still reaches a chat once, so it shows which source won.
    show_source: bool = False
    # Experiment mode: a chat gets one alert per matching search instead of one per job, and the later one
    # says how many seconds after the first it arrived. Off in normal use.
    duplicates_per_source: bool = False
    live_tag: str = ""  # put in front of live alerts, e.g. "[new] " while the legacy monitor also sends
    show_description_chars: int = Field(default=300, ge=0)


class AlertsConfig(_Model):
    enabled: bool = True
    chat_id: Optional[int] = None  # where health alerts go; null = TELEGRAM_CHAT_ID from .env
    check_interval_s: float = Field(default=60, gt=0)
    stale_search_minutes: float = Field(default=5, gt=0)  # no successful poll for this long = problem
    failing_search_after: int = Field(default=3, ge=1)  # consecutive failed polls
    queue_backlog_max: int = Field(default=200, ge=1)
    repeat_after_minutes: float = Field(default=60, gt=0)  # remind about a problem that is still open
    # Local time ("HH:MM") of the once-a-day summary; null = none. It also proves the alerting is alive.
    daily_summary_at: Optional[str] = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class DashboardConfig(_Model):
    host: str = "127.0.0.1"  # read-only status page; keep it on localhost unless you add auth in front
    port: int = Field(default=8787, ge=1, le=65535)
    # Name of the environment variable holding the page's password (HTTP Basic, any user name).
    # Required when `host` is not a loopback address; /healthz never asks for it.
    password_env: str = "DASHBOARD_PASSWORD"


class BackupConfig(_Model):
    enabled: bool = True  # false = `scraper backup` does nothing and a missing backup is not a problem
    dir: Optional[str] = None  # null = a "<repo>-backups" folder next to the repository
    keep_days: float = Field(default=14, gt=0)
    copy_to: Optional[str] = None  # second folder for each dump, e.g. one synced to a cloud drive; null = off
    max_age_hours: float = Field(default=30, gt=0)  # newest dump older than this = health warning
    pg_bin_dir: Optional[str] = None  # where pg_dump/pg_restore live, if not on PATH
    timeout_s: float = Field(default=900, gt=0)


class LogConfig(_Model):
    # `scraper up --log-file`: when the file passes max_mb at start-up it is rotated (file.1, file.2, ...).
    max_mb: float = Field(default=20, gt=0)
    keep_files: int = Field(default=5, ge=1)


class BotConfig(_Model):
    enabled: bool = False  # whether `scraper up` also starts the Telegram bot
    # Who may use the bot. `allowlist` = only allowed_chat_ids (+ TELEGRAM_CHAT_ID); `open` = anyone.
    # A paid-subscription check can replace this later (app/scraper/bot.py::AccessPolicy).
    access: Literal["allowlist", "open"] = "allowlist"
    allowed_chat_ids: List[int] = Field(default_factory=list)
    max_searches_per_chat: int = Field(default=5, ge=1)
    commands_per_minute: int = Field(default=20, ge=1)  # per chat; more than this is answered with "slow down"


class ScraperConfig(_Model):
    upwork: UpworkConfig
    transports: Dict[str, TransportConfig]
    poller: PollerConfig
    tokens: TokensConfig
    minters: List[MinterConfig] = Field(min_length=1)
    failure_policy: FailurePolicyConfig
    rate_limit: RateLimitConfig
    backend: Literal["redis", "memory"]
    egress_id: str = "default"
    dispatcher: DispatcherConfig
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    retention_days: int = Field(gt=0)

    @model_validator(mode="after")
    def _references_resolve(self) -> "ScraperConfig":
        known = set(self.transports)
        if self.poller.transport not in known:
            raise ValueError(f"poller.transport {self.poller.transport!r} is not defined under transports")
        if self.poller.fallback_transport and self.poller.fallback_transport not in known:
            raise ValueError(f"poller.fallback_transport {self.poller.fallback_transport!r} is not defined under transports")
        names = [m.name for m in self.minters]
        if len(names) != len(set(names)):
            raise ValueError("minter names must be unique")
        for m in self.minters:
            if m.kind == "http" and m.transport not in known:
                raise ValueError(f"minter {m.name!r}: transport {m.transport!r} is not defined under transports")
        if self.poller.details_count > self.poller.ids_count:
            raise ValueError("poller.details_count cannot exceed poller.ids_count")
        return self


def load_scraper_config(path: str | Path | None = None) -> ScraperConfig:
    resolved = Path(path) if path else Path(os.environ.get(ENV_PATH_KEY) or DEFAULT_PATH)
    if not resolved.exists():
        raise ConfigError(f"scraper config not found: {resolved}")
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        return ScraperConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as ex:
        raise ConfigError(f"invalid scraper config {resolved}:\n{ex}") from ex
