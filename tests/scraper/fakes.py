"""Test doubles shared by the scraper tests."""
from __future__ import annotations

from typing import Callable, List, Optional, Union

from app.scraper.transports.base import HttpResponse

Scripted = Union[HttpResponse, Exception, Callable[[], HttpResponse]]


class FakeTransport:
    """Returns scripted responses in order; records what it was asked."""

    def __init__(self, name: str = "fake", *, pages: Optional[List[Scripted]] = None, api: Optional[List[Scripted]] = None):
        self.name = name
        self._pages = list(pages or [])
        self._api = list(api or [])
        self.page_calls: List[str] = []
        self.api_calls: List[dict] = []
        self.closed = False

    @staticmethod
    def _next(queue: List[Scripted]) -> HttpResponse:
        if not queue:
            raise AssertionError("FakeTransport ran out of scripted responses")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item() if callable(item) else item

    def get_page(self, url: str) -> HttpResponse:
        self.page_calls.append(url)
        return self._next(self._pages)

    def post_api(self, url: str, token: str, body: str) -> HttpResponse:
        self.api_calls.append({"url": url, "token": token, "body": body})
        return self._next(self._api)

    def close(self) -> None:
        self.closed = True


class FakeTokens:
    """TokenSource that hands out t1, t2, ... and counts invalidations."""

    def __init__(self) -> None:
        self.n = 1
        self.invalidated: List[str] = []

    def get(self) -> str:
        return f"t{self.n}"

    def invalidate(self, value: str) -> None:
        self.invalidated.append(value)
        self.n += 1


def ok(text: str) -> HttpResponse:
    return HttpResponse(200, text, {"content-type": "application/json"})


def status(code: int, text: str = "", **headers: str) -> HttpResponse:
    return HttpResponse(code, text, {k.replace("_", "-"): v for k, v in headers.items()})
