from __future__ import annotations

from typing import Optional

from curl_cffi import requests as creq
from curl_cffi.requests.exceptions import RequestException

from app.scraper.config import TransportConfig
from app.scraper.errors import Transient
from app.scraper.transports.base import HttpResponse

_ORIGIN = "https://www.upwork.com"


class CurlCffiTransport:
    """curl with a browser's TLS/HTTP2 fingerprint. The library sends headers matching the profile;
    adding hand-written browser headers that disagree with it gets the request challenged."""

    def __init__(self, name: str, cfg: TransportConfig) -> None:
        self.name = name
        self._cfg = cfg
        self._api: Optional[creq.Session] = None

    def _session(self) -> creq.Session:
        proxies = {"https": self._cfg.proxy, "http": self._cfg.proxy} if self._cfg.proxy else None
        return creq.Session(impersonate=self._cfg.impersonate, proxies=proxies)

    def get_page(self, url: str) -> HttpResponse:
        session = self._session()
        try:
            r = session.get(url, timeout=self._cfg.timeout_s)
            return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()}, dict(session.cookies))
        except RequestException as ex:
            raise Transient(f"{self.name} get_page: {ex!r}") from ex
        finally:
            session.close()

    def post_api(self, url: str, token: str, body: str) -> HttpResponse:
        if self._api is None:
            self._api = self._session()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": _ORIGIN,
            "Referer": f"{_ORIGIN}/nx/search/jobs/",
        }
        try:
            r = self._api.post(url, data=body, headers=headers, timeout=self._cfg.timeout_s)
            return HttpResponse(r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()})
        except RequestException as ex:
            self.close()  # drop the possibly broken connection
            raise Transient(f"{self.name} post_api: {ex!r}") from ex

    def close(self) -> None:
        if self._api is not None:
            try:
                self._api.close()
            finally:
                self._api = None
