# app/ingest/upwork_cm.py
from __future__ import annotations

from typing import Sequence
from urllib.parse import quote_plus

# Modified import to use browser_cm with Camoufox
from app.shared.browser_cm import fetch_html_with_waits, camoufox_session
from app.config import AppConfig

# Upwork-specific base
BASE_URL = "https://www.upwork.com"


def build_or_query(terms: Sequence[str], quote_terms: bool) -> str:
    """
    Build an OR query for Upwork search.
    Example:
        ["data engineer", "etl"] -> '"data engineer" OR etl' (if quote_terms=True)
    """
    cleaned = [t.strip() for t in terms if t and t.strip()]
    if not cleaned:
        return ""

    if quote_terms:
        cleaned = [f'"{t}"' for t in cleaned]

    return " OR ".join(cleaned)


def build_search_url(base_search_url: str, query: str) -> str:
    """
    Build the Upwork search URL for the given query.
    """
    # Upwork uses q= in /nx/search/jobs/
    return f"{base_search_url}?q={quote_plus(query)}&sort=recency"


def fetch_upwork_search_html(cfg: AppConfig, terms: Sequence[str]) -> str:
    """
    Upwork-specific entrypoint:
    - build query + URL
    - fetch HTML using shared browser logic
    """
    query = build_or_query(terms, quote_terms=cfg.upwork.quote_terms)
    url = build_search_url(cfg.upwork.base_search_url, query)

    # Modified to pass camufoux config if available
    camufoux_cfg = getattr(cfg, "camufoux", None)
    
    with camoufox_session(cfg.selenium, camufoux_cfg=camufoux_cfg) as page:
        return fetch_html_with_waits(page, url, cfg.waits)
