"""
Failure taxonomy. Each type calls for a different reaction, so callers branch on the class,
never on status codes or message text:

  Challenged   -> the client's fingerprint was rejected; switch client, do not retry it
  RateLimited  -> the IP is over budget; back off, switching client makes it worse
  AuthExpired  -> the token is no longer accepted; mint a new one
  Transient    -> server/network hiccup; retry the same thing after a pause
"""
from __future__ import annotations

from typing import Optional


class ScraperError(Exception):
    """Base class for everything the scraper raises on purpose."""


class Challenged(ScraperError):
    """Cloudflare challenge / block (HTTP 403)."""


class RateLimited(ScraperError):
    """HTTP 429."""

    def __init__(self, message: str = "", *, retry_after_s: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AuthExpired(ScraperError):
    """The API rejected the token (HTTP 401 or a GraphQL auth error)."""


class Transient(ScraperError):
    """5xx, timeout, connection error, or an unparsable response."""


class MintChainExhausted(ScraperError):
    """Every configured minter failed."""


class ConfigError(ScraperError):
    """The scraper configuration is invalid."""
