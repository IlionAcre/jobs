from __future__ import annotations

import sys
import threading

import pytest

from app.scraper.config import FailurePolicyConfig, MinterConfig, TokensConfig, UpworkConfig
from app.scraper.errors import Challenged, MintChainExhausted, RateLimited, Transient
from app.scraper.models import Token
from app.scraper.tokens.chain import MinterChain
from app.scraper.tokens.manager import TokenManager
from app.scraper.tokens.minters import HttpMinter, SubprocessMinter, mint_url
from app.scraper.tokens.store import MemoryTokenStore
from app.scraper.transports.base import HttpResponse

from .fakes import FakeTransport, status

UPWORK = UpworkConfig(
    search_page_url="https://www.upwork.com/nx/search/jobs/",
    api_url="https://www.upwork.com/api/graphql/v1?alias=visitorJobSearch",
    token_cookie="UniversalSearchNuxt_vt",
    job_url_template="https://www.upwork.com/jobs/{job_id}",
)
POLICY = FailurePolicyConfig(transient_retries=2, transient_backoff_s=[5, 20], rate_limit_backoff_s=120)
TOKENS = TokensConfig(refresh_after_hours=10, max_age_hours=14)
HOUR = 3600.0


def page(token="oauth2v2_abc"):
    return HttpResponse(200, "<html>", {}, {"UniversalSearchNuxt_vt": token} if token else {})


class Scripted:
    """Minter whose mint() plays back a list of tokens / exceptions."""

    def __init__(self, name, *outcomes):
        self.name, self._outcomes, self.calls = name, list(outcomes), 0

    def mint(self):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now, self.slept = now, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def chain(*minters, clock=None):
    clock = clock or Clock()
    return MinterChain(minters, POLICY, sleep=clock.sleep, clock=clock), clock


# --- HttpMinter -------------------------------------------------------------------------------

def test_http_minter_reads_cookie_from_search_page():
    t = FakeTransport(pages=[page()])
    assert HttpMinter("m", t, UPWORK).mint() == "oauth2v2_abc"
    assert t.page_calls == [mint_url(UPWORK)] and "sort=recency" in t.page_calls[0]


@pytest.mark.parametrize("resp, error", [
    (status(403, cf_mitigated="challenge"), Challenged),
    (status(429), RateLimited),
    (status(503), Transient),
    (page(token=None), Transient),          # 200 but no cookie
    (page(token="garbage"), Transient),     # cookie that is not a token
])
def test_http_minter_error_mapping(resp, error):
    with pytest.raises(error):
        HttpMinter("m", FakeTransport(pages=[resp]), UPWORK).mint()


# --- MinterChain failure policy ---------------------------------------------------------------

def test_chain_first_minter_wins():
    a, b = Scripted("a", "oauth2v2_a"), Scripted("b", "oauth2v2_b")
    c, clock = chain(a, b)
    token = c.mint()
    assert (token.value, token.minter, token.minted_at) == ("oauth2v2_a", "a", clock.now) and b.calls == 0


def test_challenge_moves_to_next_minter_without_retry_or_wait():
    a, b = Scripted("a", Challenged("x")), Scripted("b", "oauth2v2_b")
    c, clock = chain(a, b)
    assert c.mint().minter == "b"
    assert a.calls == 1 and clock.slept == []


def test_transient_is_retried_with_backoff_then_falls_through():
    a = Scripted("a", Transient("503"), Transient("503"), Transient("503"))
    b = Scripted("b", "oauth2v2_b")
    c, clock = chain(a, b)
    assert c.mint().minter == "b"
    assert a.calls == 3 and clock.slept == [5, 20]  # transient_retries=2 -> 3 attempts, 2 waits


def test_transient_then_success_stays_on_same_minter():
    a, b = Scripted("a", Transient("503"), "oauth2v2_a"), Scripted("b", "oauth2v2_b")
    c, clock = chain(a, b)
    assert c.mint().minter == "a" and b.calls == 0 and clock.slept == [5]


def test_rate_limit_stops_the_chain():
    a, b = Scripted("a", RateLimited("429")), Scripted("b", "oauth2v2_b")
    c, _ = chain(a, b)
    with pytest.raises(RateLimited):
        c.mint()
    assert b.calls == 0  # never advance on an IP limit


def test_all_minters_failing_raises_exhausted():
    c, _ = chain(Scripted("a", Challenged("x")), Scripted("b", Challenged("y")))
    with pytest.raises(MintChainExhausted) as err:
        c.mint()
    assert "a: challenged" in str(err.value) and "b: challenged" in str(err.value)


# --- TokenManager -----------------------------------------------------------------------------

def manager(*outcomes, store=None, clock=None):
    clock = clock or Clock()
    m = Scripted("m", *outcomes)
    c = MinterChain([m], POLICY, sleep=clock.sleep, clock=clock)
    store = store or MemoryTokenStore()
    return TokenManager(store, c, TOKENS, clock=clock, sleep=clock.sleep), m, store, clock


def test_first_get_mints_then_reuses():
    mgr, m, _, _ = manager("oauth2v2_1")
    assert mgr.get() == "oauth2v2_1" and mgr.get() == "oauth2v2_1" and m.calls == 1


def test_refreshes_after_refresh_age_while_old_token_still_valid():
    mgr, m, _, clock = manager("oauth2v2_1", "oauth2v2_2")
    mgr.get()
    clock.now += 9 * HOUR
    assert mgr.get() == "oauth2v2_1" and m.calls == 1      # not yet
    clock.now += 2 * HOUR                                   # 11 h > refresh_after 10 h
    assert mgr.get() == "oauth2v2_2" and m.calls == 2


def test_failed_early_refresh_keeps_old_token_and_backs_off():
    mgr, m, _, clock = manager("oauth2v2_1", Challenged("x"), "oauth2v2_2")
    mgr.get()
    clock.now += 11 * HOUR
    assert mgr.get() == "oauth2v2_1" and m.calls == 2       # refresh failed, old token still served
    assert mgr.get() == "oauth2v2_1" and m.calls == 2       # no hammering right away
    clock.now += 601
    assert mgr.get() == "oauth2v2_2" and m.calls == 3


def test_token_past_max_age_is_not_served():
    mgr, m, _, clock = manager("oauth2v2_1", "oauth2v2_2")
    mgr.get()
    clock.now += 15 * HOUR
    assert mgr.get() == "oauth2v2_2"


def test_invalidate_forces_a_new_mint_but_ignores_stale_values():
    mgr, m, store, _ = manager("oauth2v2_1", "oauth2v2_2")
    mgr.get()
    mgr.invalidate("oauth2v2_something_else")               # another worker already replaced it
    assert store.get("default").value == "oauth2v2_1"
    mgr.invalidate("oauth2v2_1")
    assert mgr.get() == "oauth2v2_2" and m.calls == 2


def test_mint_failure_with_no_token_propagates():
    mgr, _, _, _ = manager(Challenged("x"))
    with pytest.raises(MintChainExhausted):
        mgr.get()


def test_before_mint_hook_runs_before_each_mint():
    calls = []
    clock = Clock()
    c = MinterChain([Scripted("m", "oauth2v2_1")], POLICY, sleep=clock.sleep, clock=clock)
    TokenManager(MemoryTokenStore(), c, TOKENS, clock=clock, before_mint=lambda: calls.append(1)).get()
    assert calls == [1]


def test_concurrent_gets_mint_once():
    started, release = threading.Event(), threading.Event()

    class Slow:
        name, calls = "slow", 0

        def mint(self):
            Slow.calls += 1
            started.set()
            release.wait(5)
            return "oauth2v2_1"

    store = MemoryTokenStore()
    c = MinterChain([Slow()], POLICY)
    results = []
    workers = [threading.Thread(target=lambda: results.append(
        TokenManager(store, c, TOKENS, sleep=lambda s: release.wait(0.01)).get())) for _ in range(5)]
    for w in workers:
        w.start()
    started.wait(5)
    release.set()
    for w in workers:
        w.join(10)
    assert results == ["oauth2v2_1"] * 5 and Slow.calls == 1


# --- SubprocessMinter -------------------------------------------------------------------------

def _script(tmp_path, body):
    path = tmp_path / "mint.py"
    path.write_text("import json, sys\n" + body, encoding="utf-8")
    return MinterConfig(name="ext", kind="subprocess", python=sys.executable, script=str(path), timeout_s=20)


def test_subprocess_minter_reads_result_line_and_passes_url(tmp_path):
    cfg = _script(tmp_path, "print('noise'); print(json.dumps({'result': {'ok': True, 'token': 'oauth2v2_' + str('--url' in sys.argv)}}))")
    assert SubprocessMinter(cfg, UPWORK).mint() == "oauth2v2_True"


@pytest.mark.parametrize("result, error", [
    ("{'ok': False, 'token': None, 'challenge_seen': True}", Challenged),
    ("{'ok': False, 'token': None, 'challenge_types': ['managed']}", Challenged),
    ("{'ok': False, 'token': None, 'status': 403}", Challenged),
    ("{'ok': False, 'token': None, 'status': 429}", RateLimited),
    ("{'ok': False, 'token': None, 'error': 'boom'}", Transient),
])
def test_subprocess_minter_error_mapping(tmp_path, result, error):
    cfg = _script(tmp_path, f"print(json.dumps({{'result': {result}}}))")
    with pytest.raises(error):
        SubprocessMinter(cfg, UPWORK).mint()


def test_subprocess_minter_without_result_line_is_transient(tmp_path):
    with pytest.raises(Transient):
        SubprocessMinter(_script(tmp_path, "sys.exit(3)"), UPWORK).mint()


def test_subprocess_minter_missing_script_is_transient(tmp_path):
    cfg = MinterConfig(name="ext", kind="subprocess", python=sys.executable, script=str(tmp_path / "nope.py"))
    with pytest.raises(Transient):
        SubprocessMinter(cfg, UPWORK).mint()


def test_subprocess_minter_refuses_when_memory_is_short(tmp_path):
    cfg = _script(tmp_path, "print(json.dumps({'result': {'ok': True, 'token': 'oauth2v2_x'}}))")
    cfg = cfg.model_copy(update={"min_free_mem_mb": 10**9})
    with pytest.raises(Transient) as err:
        SubprocessMinter(cfg, UPWORK).mint()
    assert "MB free" in str(err.value)


def test_subprocess_minter_retries_up_to_attempts(tmp_path):
    counter = tmp_path / "n"
    body = (f"p = r'{counter}'\n"
            "import os\n"
            "n = int(open(p).read()) if os.path.exists(p) else 0\n"
            "open(p, 'w').write(str(n + 1))\n"
            "print(json.dumps({'result': {'ok': n >= 2, 'token': 'oauth2v2_x' if n >= 2 else None, 'challenge_seen': n < 2}}))")
    cfg = _script(tmp_path, body).model_copy(update={"attempts": 3})
    assert SubprocessMinter(cfg, UPWORK).mint() == "oauth2v2_x" and counter.read_text() == "3"
