"""
Opt-in checks against the real site:  python -m pytest -m live

Three requests in total (one search page, two API calls). Run it after bumping the curl_cffi profile or
when alerts stop, to see whether the primary path still works. Not part of the normal test run.
"""
from __future__ import annotations

import pytest

from app.scraper.config import load_scraper_config
from app.scraper.factory import build_search_client, build_token_manager
from app.scraper.tokens.store import MemoryTokenStore

pytestmark = pytest.mark.live


def test_primary_minter_and_both_poll_tiers_work():
    cfg = load_scraper_config()
    primary_only = cfg.model_copy(update={"minters": cfg.minters[:1], "backend": "memory"})
    tokens = build_token_manager(primary_only, MemoryTokenStore())
    client = build_search_client(primary_only, tokens)
    try:
        refs = client.search_ids("python OR scraping")
        assert len(refs) == cfg.poller.ids_count and all(r.job_id.startswith("~") and r.publish_time for r in refs)
        assert tokens.current().minter == cfg.minters[0].name

        jobs = client.search_details("python OR scraping", count=3)
        assert [j.job_id for j in jobs] == [r.job_id for r in refs[:3]] or len(jobs) == 3  # a job may land between calls
        assert all(j.title and j.description and j.url.endswith(j.job_id) for j in jobs)
        assert client.transport.name == cfg.poller.transport  # no fallback was needed
    finally:
        client.close()
