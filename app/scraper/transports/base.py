"""
A Transport is one way of talking HTTP to Upwork (one client library + one fingerprint).

Two operations, because the site treats them differently:
  get_page  - a cold navigation to the search page (used to mint a token; fresh session every time)
  post_api  - a GraphQL call with a Bearer token (keep-alive session; a new connection costs ~2 s)

Implementations translate status codes into the scraper's error taxonomy via `raise_for_status`,
so nothing above this layer looks at HTTP details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from app.scraper.errors import AuthExpired, Challenged, RateLimited, Transient


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    text: str
    headers: Dict[str, str] = field(default_factory=dict)  # lower-cased names
    cookies: Dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    name: str

    def get_page(self, url: str) -> HttpResponse: ...

    def post_api(self, url: str, token: str, body: str) -> HttpResponse: ...

    def close(self) -> None: ...


def _retry_after(headers: Dict[str, str]) -> Optional[float]:
    try:
        return float(headers.get("retry-after", ""))
    except ValueError:
        return None


def raise_for_status(resp: HttpResponse, what: str) -> HttpResponse:
    """Map an HTTP outcome to the error taxonomy; return the response when it is a 200."""
    if resp.status == 200:
        return resp
    detail = f"{what}: HTTP {resp.status}"
    if resp.status == 403:
        raise Challenged(detail)
    if resp.status == 429:
        raise RateLimited(detail, retry_after_s=_retry_after(resp.headers))
    if resp.status == 401:
        raise AuthExpired(detail)
    raise Transient(detail)
