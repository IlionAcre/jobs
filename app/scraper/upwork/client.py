from __future__ import annotations

import logging
from typing import Callable, List, Protocol, Sequence, TypeVar

from app.scraper.config import ScraperConfig
from app.scraper.errors import AuthExpired, Challenged
from app.scraper.models import Job, JobRef
from app.scraper.transports.base import Transport, raise_for_status
from app.scraper.upwork.parse import parse_jobs, parse_refs
from app.scraper.upwork.queries import DETAILS_SELECTION, IDS_SELECTION, build_body

log = logging.getLogger("scraper.upwork")
T = TypeVar("T")


class TokenSource(Protocol):
    def get(self) -> str: ...

    def invalidate(self, value: str) -> None: ...


class UpworkSearchClient:
    """search_ids() / search_details() against the visitor search API.

    Handles the two recoverable failures itself so callers don't have to:
      token rejected  -> invalidate it, get a fresh one, retry once
      client blocked  -> move to the next transport (if any) and retry once
    Rate limits and transient errors propagate; the fetcher decides how long to wait.
    """

    def __init__(self, config: ScraperConfig, transports: Sequence[Transport], tokens: TokenSource) -> None:
        if not transports:
            raise ValueError("at least one transport is required")
        self._cfg = config
        self._transports = list(transports)
        self._active = 0
        self._tokens = tokens

    @property
    def transport(self) -> Transport:
        return self._transports[self._active]

    def search_ids(self, query_text: str) -> List[JobRef]:
        body = build_body(query_text, count=self._cfg.poller.ids_count, selection=IDS_SELECTION)
        return self._call(body, parse_refs)

    def search_details(self, query_text: str, count: int) -> List[Job]:
        body = build_body(query_text, count=count, selection=DETAILS_SELECTION)
        return self._call(body, lambda text: parse_jobs(text, job_url_template=self._cfg.upwork.job_url_template))

    def _call(self, body: str, parse: Callable[[str], T]) -> T:
        try:
            return self._attempt(body, parse)
        except AuthExpired:
            log.info("token_rejected_retrying")
            return self._attempt(body, parse)  # _attempt already invalidated the rejected token
        except Challenged:
            if self._active + 1 >= len(self._transports):
                raise
            blocked = self.transport.name
            self._active += 1
            log.warning("api_challenged_switching_transport", extra={"from": blocked, "to": self.transport.name})
            return self._attempt(body, parse)

    def _attempt(self, body: str, parse: Callable[[str], T]) -> T:
        token = self._tokens.get()
        try:
            resp = raise_for_status(self.transport.post_api(self._cfg.upwork.api_url, token, body), "search api")
            return parse(resp.text)  # a 200 can still carry a GraphQL auth error; the parser raises AuthExpired
        except AuthExpired:
            self._tokens.invalidate(token)
            raise

    def close(self) -> None:
        for transport in self._transports:
            transport.close()
