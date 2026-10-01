"""Health rules, watchdog alerts, presence, dashboard rendering and the Telegram bot's command logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.scraper.bot import AllowlistPolicy, BotCore, OpenPolicy, build_policy, compile_query, parse_filter
from app.scraper.config import ScraperConfig
from app.scraper.dashboard import build_status, render_html
from app.scraper.health import HealthSnapshot, evaluate
from app.scraper.models import Token
from app.scraper.pipeline.watchdog import Watchdog
from app.scraper.presence import Heartbeat, MemoryPresence, RoleBeat

NOW = 1_800_000_000.0
UTC_NOW = datetime.fromtimestamp(NOW, tz=timezone.utc)


@pytest.fixture
def cfg(config_dict):
    config_dict["minters"].append({"name": "patchright", "kind": "subprocess", "python": "x", "script": "y"})
    return ScraperConfig.model_validate(config_dict)


def search(search_id=1, *, ok_s_ago=20.0, failures=0, subscribers=1, enabled=True, error=None):
    return {"search_id": search_id, "query": "python", "enabled": enabled, "primed": True, "subscribers": subscribers,
            "last_ok_at": None if ok_s_ago is None else UTC_NOW - timedelta(seconds=ok_s_ago),
            "consecutive_failures": failures, "last_error": error, "next_run_at": UTC_NOW,
            "created_at": UTC_NOW - timedelta(hours=1)}


def healthy(**overrides) -> HealthSnapshot:
    snap = HealthSnapshot(
        at=NOW,
        roles=[RoleBeat(r, f"{r}-host-1", NOW - 5) for r in ("scheduler", "fetcher", "dispatcher")],
        token=Token("oauth2v2_x", NOW - 3600, "curl_cffi"),
        searches=[search()],
    )
    for k, v in overrides.items():
        setattr(snap, k, v)
    return snap


def keys(snap, cfg):
    return sorted(p.key for p in evaluate(snap, cfg))


# --- health rules -----------------------------------------------------------------------------

def test_healthy_pipeline_has_no_problems(cfg):
    assert evaluate(healthy(), cfg) == []


@pytest.mark.parametrize("change, expected", [
    (dict(roles=[RoleBeat("scheduler", "s", NOW), RoleBeat("dispatcher", "d", NOW)]), ["role_missing:fetcher"]),
    (dict(roles=[]), ["role_missing:dispatcher", "role_missing:fetcher", "role_missing:scheduler"]),
    (dict(searches=[search(ok_s_ago=600)]), ["search_stale:1"]),
    (dict(searches=[search(ok_s_ago=None)]), ["search_stale:1"]),                       # never polled, an hour old
    (dict(searches=[search(failures=3, error="Challenged: 403")]), ["search_failing:1"]),
    (dict(searches=[search(failures=2)]), []),                                           # below the threshold
    (dict(searches=[search(ok_s_ago=600, subscribers=0)]), []),                          # nobody is waiting on it
    (dict(searches=[search(ok_s_ago=600, enabled=False)]), []),
    (dict(token=Token("oauth2v2_x", NOW - 60, "patchright")), ["token_fallback"]),
    (dict(token=Token("oauth2v2_x", NOW - 15 * 3600, "curl_cffi")), ["token_old"]),
    (dict(token=None), []),                                                              # minted on first poll
    (dict(api_paused_s=90.0), ["rate_limited"]),
    (dict(work_backlog=500), ["backlog:work"]),
    (dict(jobs_backlog=201), ["backlog:jobs"]),
    (dict(db_error="OperationalError"), ["db_down"]),
])
def test_health_rules(cfg, change, expected):
    assert keys(healthy(**change), cfg) == expected


def test_backend_down_is_reported_alone(cfg):
    # without Redis we can't see roles, token or queues, so don't also claim they are missing
    assert keys(healthy(backend_error="ConnectionError", roles=[], token=None), cfg) == ["backend_down"]


def test_failing_search_message_carries_the_error(cfg):
    (p,) = evaluate(healthy(searches=[search(failures=4, error="Challenged: mint via curl_cffi")]), cfg)
    assert "4 polls in a row" in p.message and "Challenged" in p.message and p.severity == "critical"


# --- watchdog ---------------------------------------------------------------------------------

def watchdog(cfg, snapshots):
    clock = SimpleNamespace(now=NOW)
    sent = []
    it = iter(snapshots)
    dog = Watchdog(lambda: next(it), cfg, sent.append, clock=lambda: clock.now)
    return dog, sent, clock


def test_watchdog_is_silent_while_healthy(cfg):
    dog, sent, _ = watchdog(cfg, [healthy(), healthy()])
    dog.tick()
    dog.tick()
    assert sent == []


def test_watchdog_alerts_once_reminds_later_and_announces_recovery(cfg):
    broken = lambda: healthy(roles=[RoleBeat("scheduler", "s", NOW), RoleBeat("dispatcher", "d", NOW)])  # noqa: E731
    dog, sent, clock = watchdog(cfg, [broken(), broken(), broken(), healthy()])
    dog.tick()
    assert len(sent) == 1 and sent[0].startswith("🚨 Scraper problem") and "fetcher is not running" in sent[0]
    clock.now += 60
    dog.tick()
    assert len(sent) == 1                                                    # same problem, no repeat yet
    clock.now += 3600
    dog.tick()
    assert len(sent) == 2 and sent[1].startswith("⏰ Still open") and "open for 61 min" in sent[1]
    clock.now += 120
    dog.tick()
    assert len(sent) == 3 and sent[2] == "✅ Resolved\n• fetcher not running (was open for 63 min)"


def test_watchdog_uses_warning_icon_for_non_critical_and_batches(cfg):
    snap = healthy(token=Token("oauth2v2_x", NOW, "patchright"), api_paused_s=30.0)
    dog, sent, _ = watchdog(cfg, [snap])
    dog.tick()
    assert len(sent) == 1 and sent[0].startswith("⚠️") and sent[0].count("•") == 2


def test_watchdog_survives_a_failing_sender(cfg):
    def boom(_text):
        raise RuntimeError("telegram down")
    dog = Watchdog(lambda: healthy(roles=[]), cfg, boom, clock=lambda: NOW)
    assert len(dog.tick()) == 1                                              # did not raise


# --- presence ---------------------------------------------------------------------------------

def test_memory_presence_expires_and_heartbeat_throttles():
    clock = SimpleNamespace(now=100.0)
    presence = MemoryPresence(clock=lambda: clock.now)
    beat = Heartbeat(presence, "fetcher", "f-1", every_s=20, info=lambda: {"polls": 3}, clock=lambda: clock.now)
    beat()
    beat()                                                                    # throttled
    (alive,) = presence.alive()
    assert (alive.role, alive.name, alive.at, alive.info) == ("fetcher", "f-1", 100.0, {"polls": 3})
    clock.now += 25
    beat()
    assert presence.alive()[0].at == 125.0
    clock.now += 200                                                          # role died: entry expires by itself
    assert presence.alive() == []


def test_heartbeat_never_raises():
    class Broken:
        def beat(self, *a, **k):
            raise ConnectionError("redis down")
    Heartbeat(Broken(), "fetcher", "f")()


# --- dashboard --------------------------------------------------------------------------------

ACTIVITY = {"hours": 24.0, "new_jobs": 12, "lag_median_s": 46.2, "lag_p90_s": 61.0, "deliveries": {"shadow": 12}}
RECENT = [{"seen_at": UTC_NOW, "publish_time": UTC_NOW, "lag_s": 44.6, "title": "Scrape <b>sites</b> & more",
           "url": "https://www.upwork.com/jobs/~01", "query": "python"}]


def test_dashboard_healthy_page(cfg):
    snap = healthy()
    status = build_status(snap, evaluate(snap, cfg), cfg, ACTIVITY, RECENT)
    assert status["ok"] is True and status["problems"] == [] and status["token"]["is_primary"] is True
    html = render_html(status)
    assert "All good" in html and "shadow" in html and "46 s after publish" in html
    assert "Scrape &lt;b&gt;sites&lt;/b&gt; &amp; more" in html and "<b>sites</b>" not in html   # job titles are escaped
    assert "oauth2v2_x" not in html and "oauth2v2_x" not in str(status)                           # token value never shown


def test_dashboard_shows_problems_and_missing_roles(cfg):
    snap = healthy(roles=[RoleBeat("scheduler", "s", NOW)], searches=[search(failures=5, error="Challenged")])
    status = build_status(snap, evaluate(snap, cfg), cfg, None, None)
    assert status["ok"] is False and sorted(status["missing_roles"]) == ["dispatcher", "fetcher"]
    html = render_html(status)
    assert "open problem(s)" in html and "not running" in html and "5 failed" in html and "No new jobs seen yet" in html


def test_dashboard_warning_only_keeps_ok_true(cfg):
    snap = healthy(token=Token("oauth2v2_x", NOW, "patchright"))
    status = build_status(snap, evaluate(snap, cfg), cfg, ACTIVITY, [])
    assert status["ok"] is True and len(status["problems"]) == 1 and "fallback" in render_html(status)


# --- bot --------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, compiled", [
    ("python AND scraping", "python scraping"),
    ("python or  Scraping and selenium", "python OR Scraping selenium"),
    ("OR python OR", "python"),
    ("   ", ""),
])
def test_compile_query(raw, compiled):
    assert compile_query(raw) == compiled


@pytest.mark.parametrize("spec, expected", [
    ("include: python, scraping; exclude: wordpress", (["python", "scraping"], ["wordpress"])),
    ("EXCLUDE: Alloy Scrap , wordpress", ([], ["alloy scrap", "wordpress"])),
    ("include: c++", (["c++"], [])),
    ("python scraping", None),
    ("include:", None),
])
def test_parse_filter(spec, expected):
    assert parse_filter(spec) == expected


def test_policies(cfg):
    assert AllowlistPolicy([1]).allowed(1) and not AllowlistPolicy([1]).allowed(2) and OpenPolicy().allowed(2)
    owner_only = build_policy(cfg, owner_chat_id=111)
    assert owner_only.allowed(111) and not owner_only.allowed(222)
    assert not build_policy(cfg, owner_chat_id=None).allowed(111)
    open_cfg = cfg.model_copy(update={"bot": cfg.bot.model_copy(update={"access": "open"})})
    assert build_policy(open_cfg, None).allowed(222)


@pytest.fixture
def bot(store, cfg):
    return BotCore(store, cfg, AllowlistPolicy([111, 222]))


def test_bot_ignores_plain_text_and_refuses_strangers(bot):
    assert bot.handle(111, "hello there") is None
    reply = bot.handle(999, "/search python")
    assert "private" in reply and "999" in reply
    assert bot.handle(111, "/nonsense").startswith("I don't know that command")


def test_bot_search_list_filter_remove_flow(bot, store):
    reply = bot.handle(111, "/search python AND scraping")
    assert reply.startswith("Now watching #") and "python scraping" in reply and "test mode" in reply
    sub = store.subscriptions_for_chat(111)[0]

    assert bot.handle(111, "/search@MyBot  Python   Scraping").startswith("Already watching")     # same search, bot suffix
    assert len(store.subscriptions_for_chat(111)) == 1

    assert "no filter" in bot.handle(111, f"/filter {sub.subscription_id}")
    saved = bot.handle(111, f"/filter #{sub.subscription_id} include: Python, scraper; exclude: wordpress")
    assert "Filter saved" in saved and "only if it mentions: python, scraper" in saved and "never if it mentions: wordpress" in saved
    assert store.subscriptions_for_chat(111)[0].include_words == ("python", "scraper")
    assert bot.handle(111, "/search python scraping").startswith("Already watching")
    assert store.subscriptions_for_chat(111)[0].include_words == ("python", "scraper")            # re-adding keeps the filter

    listing = bot.handle(111, "/searches")
    assert f"#{sub.subscription_id}  python scraping" in listing and "wordpress" in listing

    assert "couldn't read" in bot.handle(111, f"/filter {sub.subscription_id} python only please")
    assert "Filter removed" in bot.handle(111, f"/filter {sub.subscription_id} clear")
    assert store.subscriptions_for_chat(111)[0].include_words == ()

    assert bot.handle(111, f"/remove {sub.subscription_id}").startswith("Stopped watching")
    assert bot.handle(111, "/searches").startswith("No searches yet")


def test_bot_cannot_touch_another_chats_subscription(bot, store):
    bot.handle(222, "/search react")
    theirs = store.subscriptions_for_chat(222)[0].subscription_id
    assert "Which search?" in bot.handle(111, f"/remove {theirs}")
    assert "Which search?" in bot.handle(111, f"/filter {theirs} include: x")
    assert len(store.subscriptions_for_chat(222)) == 1 and store.subscriptions_for_chat(222)[0].include_words == ()


def test_bot_limits_searches_per_chat_and_query_length(bot, cfg):
    for i in range(cfg.bot.max_searches_per_chat):
        assert bot.handle(111, f"/search topic{i}").startswith("Now watching")
    assert "limit" in bot.handle(111, "/search one too many")
    assert bot.handle(111, "/search topic0").startswith("Already watching")                        # existing one still fine
    assert "too long" in bot.handle(222, "/search " + "x" * 300)
    assert "Tell me what to search" in bot.handle(222, "/search")


def test_bot_pause_resume_status(bot, store):
    bot.handle(111, "/search python")
    assert bot.handle(111, "/pause") == "Paused 1 search(es). /resume to start again."
    assert bot.handle(111, "/pause") == "Nothing to pause."
    assert "(paused)" in bot.handle(111, "/searches")
    assert bot.handle(111, "/status").startswith("0 active search(es), 1 paused.")
    assert bot.handle(111, "/resume") == "Resumed 1 search(es)."
    assert "Everything is running." in bot.handle(111, "/status")
    sid = store.subscriptions_for_chat(111)[0].search_id
    for _ in range(3):
        store.record_poll_error(sid, "boom")
    assert "having trouble" in bot.handle(111, "/status")


def test_bot_live_mode_has_no_test_mode_note(store, cfg):
    live = cfg.model_copy(update={"dispatcher": cfg.dispatcher.model_copy(update={"mode": "live"})})
    assert "test mode" not in BotCore(store, live, OpenPolicy()).handle(5, "/search python")
