from __future__ import annotations

from typing import Optional

import requests

from app.scraper.config import TransportConfig
from app.scraper.errors import Transient
from app.scraper.transports.base import HttpResponse


class RequestsH1Transport:
    """Plain `requests` over HTTP/1.1, announcing itself honestly as Python.

    Works for the API with nothing but Authorization + Content-Type: Cloudflare blocks *inconsistent*
    clients, and an honest Python client is consistent. Adding browser headers here gets it blocked, and
    so does HTTP/2 (which is why urllib3-future must never be installed). It cannot fetch the search
    page (always challenged), so it is an API-only fallback."""

    def __init__(self, name: str, cfg: TransportConfig) -> None:
        self.name = name
        self._cfg = cfg
        self._api: Optional[requests.Session] = None

    def _proxies(self):
        return {"https": self._cfg.proxy, "http": self._cfg.proxy} if self._cfg.proxy else None

    def get_page(self, url: str) -> HttpResponse:
        try:
            with requests.Session() as session:
                r = session.get(url, timeout=self._cfg.timeout_s, proxies=self._proxies())
                return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()},
                                    session.cookies.get_dict())
        except requests.RequestException as ex:
            raise Transient(f"{self.name} get_page: {ex!r}") from ex

    def post_api(self, url: str, token: str, body: str) -> HttpResponse:
        if self._api is None:
            self._api = requests.Session()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            r = self._api.post(url, data=body, headers=headers, timeout=self._cfg.timeout_s, proxies=self._proxies())
            return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()})
        except requests.RequestException as ex:
            self.close()
            raise Transient(f"{self.name} post_api: {ex!r}") from ex

    def close(self) -> None:
        if self._api is not None:
            try:
                self._api.close()
            finally:
                self._api = None
