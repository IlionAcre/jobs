"""The all-jobs collector: per-search overrides, larger pages, and the local query matcher."""
from __future__ import annotations

import pytest

from app.scraper.query_match import matches_query, parse_query


@pytest.mark.parametrize("query, text, expected", [
    ("python OR scraping", "need a python dev", True),
    ("python OR scraping", "web scraping job", True),
    ("python OR scraping", "need a scraper", False),                 # literal: no stemming
    ("python scraping", "python and scraping", True),                # space = AND
    ("python scraping", "python only", False),
    ("python AND scraping", "scraping with python", True),
    ('"web scraping"', "we need web scraping", True),
    ('"web scraping"', "web based scraping", False),
    ("python -wordpress", "python plugin", True),
    ("python -wordpress", "python wordpress plugin", False),
    ("python NOT wordpress", "python wordpress", False),
    ("(python OR django) api", "django rest api", True),
    ("(python OR django) api", "django site", False),
    ("c++", "senior c++ engineer", True),
    ("", "anything at all", True),                                    # empty query = everything
    ("Python", "PYTHON", True),
])
def test_matches_query(query, text, expected):
    assert matches_query(query, text.lower()) is expected


def test_parse_query_shapes():
    assert parse_query("a OR b") == [[[(False, "a")], [(False, "b")]]]
    assert parse_query('"x y" -z') == [[[(False, "x y"), (True, "z")]]]


def test_always_poll_search_is_due_without_subscribers_and_keeps_its_page(store):
    plain = store.upsert_search("python")
    collector = store.upsert_search("", poll_seconds=10, always_poll=True, ids_count=50)
    due = {s.search_id: s for s in store.due_searches()}
    assert collector in due and plain not in due                       # nobody subscribed to "python"
    assert due[collector].ids_count == 50 and due[collector].always_poll
    store.upsert_search("")                                            # re-upserting keeps the settings
    again = store.get_search(collector)
    assert again.always_poll and again.ids_count == 50 and again.poll_seconds == 10
    store.set_search_enabled(collector, False)
    assert collector not in {s.search_id for s in store.due_searches()}
