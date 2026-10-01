from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.scraper.config import ScraperConfig
from app.scraper.errors import AuthExpired, Challenged, RateLimited, Transient
from app.scraper.upwork.client import UpworkSearchClient
from app.scraper.upwork.parse import parse_jobs, parse_refs

from .conftest import fixture_text
from .fakes import FakeTokens, FakeTransport, ok, status

URL_TEMPLATE = "https://www.upwork.com/jobs/{job_id}"


def test_parse_refs():
    refs = parse_refs(fixture_text("search_ids.json"))
    assert len(refs) == 4
    assert refs[0].job_id.startswith("~02")
    assert refs[0].publish_time.tzinfo is not None


def test_parse_jobs_maps_fixed_and_hourly():
    jobs = parse_jobs(fixture_text("search_details.json"), job_url_template=URL_TEMPLATE)
    fixed, hourly, hourly_no_budget = jobs[0], jobs[1], jobs[2]

    assert fixed.job_type == "fixed" and fixed.fixed_amount == 300.0 and fixed.hourly_min is None
    assert fixed.url == f"https://www.upwork.com/jobs/{fixed.job_id}"
    assert fixed.tier == "expert"

    assert hourly.job_type == "hourly" and (hourly.hourly_min, hourly.hourly_max) == (25.0, 65.0)
    assert hourly.fixed_amount is None and hourly.workload == "Less than 30 hrs/week"

    assert hourly_no_budget.hourly_min is None and hourly_no_budget.hourly_max is None
    assert all(j.title and j.description and isinstance(j.skills, tuple) for j in jobs)
    assert all(isinstance(j.publish_time, datetime) and j.publish_time.tzinfo == timezone.utc for j in jobs)
    assert "H^" not in (jobs[0].title + jobs[0].description)


@pytest.mark.parametrize(
    "text, error",
    [
        ("<html>not json</html>", Transient),
        (json.dumps({"errors": [{"message": "Authentication failed"}]}), AuthExpired),
        (json.dumps({"errors": [{"message": "boom"}]}), Transient),
        (json.dumps({"data": {"search": None}}), Transient),
    ],
)
def test_parse_error_shapes(text, error):
    with pytest.raises(error):
        parse_refs(text)


def _client(config_dict, primary, fallback=None, tokens=None):
    cfg = ScraperConfig.model_validate(config_dict)
    transports = [primary] + ([fallback] if fallback else [])
    return UpworkSearchClient(cfg, transports, tokens or FakeTokens())


def test_search_ids_sends_light_query_with_token(config_dict):
    t = FakeTransport(api=[ok(fixture_text("search_ids.json"))])
    refs = _client(config_dict, t).search_ids("python OR scraping")
    assert len(refs) == 4
    call = t.api_calls[0]
    body = json.loads(call["body"])
    assert call["token"] == "t1"
    assert body["variables"]["requestVariables"] == {
        "userQuery": "python OR scraping", "sort": "recency", "highlight": False, "paging": {"offset": 0, "count": 10}}
    assert "description" not in body["query"] and "publishTime" in body["query"]


def test_search_details_asks_for_requested_count(config_dict):
    t = FakeTransport(api=[ok(fixture_text("search_details.json"))])
    jobs = _client(config_dict, t).search_details("python", count=3)
    assert len(jobs) == 4 and json.loads(t.api_calls[0]["body"])["variables"]["requestVariables"]["paging"]["count"] == 3


def test_expired_token_is_invalidated_and_retried_once(config_dict):
    tokens = FakeTokens()
    t = FakeTransport(api=[status(401, fixture_text("auth_failed.json")), ok(fixture_text("search_ids.json"))])
    refs = _client(config_dict, t, tokens=tokens).search_ids("python")
    assert len(refs) == 4
    assert tokens.invalidated == ["t1"] and [c["token"] for c in t.api_calls] == ["t1", "t2"]


def test_graphql_auth_error_inside_200_is_treated_as_expired(config_dict):
    tokens = FakeTokens()
    t = FakeTransport(api=[ok(json.dumps({"errors": [{"message": "Authentication failed"}]})), ok(fixture_text("search_ids.json"))])
    assert len(_client(config_dict, t, tokens=tokens).search_ids("python")) == 4
    assert tokens.invalidated == ["t1"]


def test_second_auth_failure_propagates(config_dict):
    t = FakeTransport(api=[status(401), status(401)])
    with pytest.raises(AuthExpired):
        _client(config_dict, t).search_ids("python")


def test_challenge_switches_to_fallback_transport_and_stays_there(config_dict):
    primary = FakeTransport("primary", api=[status(403, "<html>Just a moment</html>")])
    fallback = FakeTransport("fallback", api=[ok(fixture_text("search_ids.json")), ok(fixture_text("search_ids.json"))])
    client = _client(config_dict, primary, fallback)
    assert len(client.search_ids("python")) == 4
    assert len(client.search_ids("python")) == 4
    assert len(primary.api_calls) == 1 and len(fallback.api_calls) == 2
    assert client.transport.name == "fallback"


def test_challenge_without_fallback_propagates(config_dict):
    with pytest.raises(Challenged):
        _client(config_dict, FakeTransport(api=[status(403)])).search_ids("python")


def test_rate_limit_and_server_errors_propagate_untouched(config_dict):
    t = FakeTransport(api=[status(429, retry_after="30"), status(503)])
    client = _client(config_dict, t, FakeTransport("fallback"))
    with pytest.raises(RateLimited) as err:
        client.search_ids("python")
    assert err.value.retry_after_s == 30.0
    with pytest.raises(Transient):
        client.search_ids("python")
    assert client.transport.name == "fake"  # neither is a reason to switch client
