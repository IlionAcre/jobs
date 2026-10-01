"""HTTP clients. Add one: write a class with the Transport methods, register its `kind` in build_transport."""
from __future__ import annotations

from app.scraper.config import ScraperConfig, TransportConfig
from app.scraper.transports.base import HttpResponse, Transport, raise_for_status


def build_transport(name: str, cfg: TransportConfig) -> Transport:
    # Imported lazily so a process only loads the client libraries it actually uses.
    if cfg.kind == "curl_cffi":
        from app.scraper.transports.curl_cffi_t import CurlCffiTransport
        return CurlCffiTransport(name, cfg)
    if cfg.kind == "httpcloak":
        from app.scraper.transports.httpcloak_t import HttpCloakTransport
        return HttpCloakTransport(name, cfg)
    if cfg.kind == "requests_h1":
        from app.scraper.transports.requests_t import RequestsH1Transport
        return RequestsH1Transport(name, cfg)
    raise ValueError(f"unknown transport kind {cfg.kind!r}")


def transport_from_config(config: ScraperConfig, name: str) -> Transport:
    return build_transport(name, config.transports[name])


__all__ = ["HttpResponse", "Transport", "build_transport", "raise_for_status", "transport_from_config"]
