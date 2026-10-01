from __future__ import annotations

from typing import Any, Dict, Optional

import httpcloak

from app.scraper.config import TransportConfig
from app.scraper.errors import Transient
from app.scraper.transports.base import HttpResponse

_ORIGIN = "https://www.upwork.com"


def _headers(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in dict(raw or {}).items():
        out[str(k).lower()] = "\n".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
    return out


class HttpCloakTransport:
    """Go-based client with its own browser presets (newer Chrome than curl_cffi, HTTP/3 capable).
    A different engine from curl_cffi, so it fails for different reasons."""

    def __init__(self, name: str, cfg: TransportConfig) -> None:
        self.name = name
        self._cfg = cfg
        self._api: Optional[httpcloak.Session] = None

    def _session(self) -> httpcloak.Session:
        kwargs: Dict[str, Any] = {"preset": self._cfg.preset, "timeout": int(self._cfg.timeout_s)}
        if self._cfg.proxy:
            kwargs["proxy"] = self._cfg.proxy
        return httpcloak.Session(**kwargs)

    def get_page(self, url: str) -> HttpResponse:
        session = self._session()
        try:
            r = session.get(url)
            cookies = {c.name: c.value for c in (session.get_cookies() or [])}  # list of Cookie objects
            return HttpResponse(int(r.status_code), r.text, _headers(r.headers), cookies)
        except Exception as ex:  # noqa: BLE001  (the library raises its own error types and plain OSError)
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
            r = self._api.post(url, data=body, headers=headers)
            return HttpResponse(int(r.status_code), r.text, _headers(r.headers))
        except Exception as ex:  # noqa: BLE001
            self.close()
            raise Transient(f"{self.name} post_api: {ex!r}") from ex

    def close(self) -> None:
        if self._api is not None:
            try:
                self._api.close()
            finally:
                self._api = None
