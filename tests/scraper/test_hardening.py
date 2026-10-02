"""Daily summary, outside health check, dashboard password, bot access commands."""
from __future__ import annotations

import base64
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.scraper.bot import BotCore, StoredAllowlistPolicy, build_policy
from app.scraper.config import ScraperConfig
from app.scraper.dashboard import check_basic_auth, is_loopback
from app.scraper.health import CRITICAL, HealthSnapshot, Problem, daily_summary
from app.scraper.models import Token
from app.scraper.pipeline.watchdog import Watchdog
from app.scraper.presence import RoleBeat

NOW = 1_800_000_000.0
OWNER, FRIEND, STRANGER = 111, 222, 333


@pytest.fixture
def cfg(config_dict):
    return ScraperConfig.model_validate(config_dict)


def snapshot() -> HealthSnapshot:
    return HealthSnapshot(at=NOW, roles=[RoleBeat("fetcher", "f", NOW)], token=Token("oauth2v2_x", NOW - 7200, "curl_cffi"),
                          searches=[{"enabled": True, "subscribers": 1}])


# --- daily summary ----------------------------------------------------------------------------

def test_daily_summary_text(cfg):
    activity = {"hours": 24, "new_jobs": 40, "lag_median_s": 42.3, "lag_p90_s": 55.0, "deliveries": {"sent": 30, "filtered": 9}}
    text = daily_summary(snapshot(), [], activity, cfg.model_copy(update={"dispatcher": cfg.dispatcher.model_copy(update={"mode": "live"})}))
    assert "40 new jobs, 30 alerts (9 filtered)" in text and "42 s after publish" in text
    assert "Token 2.0 h old, from curl_cffi" in text and "No open problems." in text and "shadow" not in text


def test_daily_summary_lists_problems_and_survives_missing_activity(cfg):
    text = daily_summary(snapshot(), [Problem("role_missing:fetcher", CRITICAL, "The fetcher is not running.")], None, cfg)
    assert "Postgres is not reachable" in text and "[critical] The fetcher is not running." in text


def make_watchdog(cfg, sent, state, at="09:00"):
    cfg = cfg.model_copy(update={"alerts": cfg.alerts.model_copy(update={"daily_summary_at": at})})
    return Watchdog(snapshot, cfg, sent.append, summary=lambda: "SUMMARY",
                    load_state=lambda: dict(state), save_state=lambda new: (state.clear(), state.update(new)))


def test_summary_is_sent_once_a_day_after_the_configured_time(cfg):
    sent, state = [], {}
    dog = make_watchdog(cfg, sent, state)
    assert dog.maybe_daily_summary(datetime(2026, 10, 2, 8, 59)) is None and sent == []
    assert dog.maybe_daily_summary(datetime(2026, 10, 2, 9, 0)) == "SUMMARY"
    assert dog.maybe_daily_summary(datetime(2026, 10, 2, 15, 0)) is None            # already sent today
    assert make_watchdog(cfg, sent, state).maybe_daily_summary(datetime(2026, 10, 2, 16, 0)) is None  # a restart doesn't repeat it
    assert dog.maybe_daily_summary(datetime(2026, 10, 3, 9, 1)) == "SUMMARY" and sent == ["SUMMARY", "SUMMARY"]


def test_summary_is_retried_when_sending_fails_and_can_be_turned_off(cfg):
    state = {}

    def failing(text):
        raise RuntimeError("telegram down")

    dog = make_watchdog(cfg, [], state)
    dog._send = failing
    with pytest.raises(RuntimeError):
        dog.maybe_daily_summary(datetime(2026, 10, 2, 10, 0))
    assert state == {}                                                               # not marked as sent
    sent = []
    assert make_watchdog(cfg, sent, state, at=None).maybe_daily_summary(datetime(2026, 10, 2, 10, 0)) is None and sent == []


def test_summary_time_is_validated(config_dict):
    config_dict["alerts"] = {"daily_summary_at": "9am"}
    with pytest.raises(ValueError):
        ScraperConfig.model_validate(config_dict)


# --- outside health check (ops/scraper/healthcheck.py, standard library only) -------------------

def load_healthcheck():
    path = Path(__file__).resolve().parents[2] / "ops" / "scraper" / "healthcheck.py"
    spec = importlib.util.spec_from_file_location("healthcheck", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_healthcheck_alerts_once_after_two_failures_and_once_on_recovery():
    hc = load_healthcheck()
    state, messages = {}, []
    for healthy in (False, False, False, True, True, False):
        state, message = hc.decide(state, healthy, "the pipeline does not answer", after=2)
        messages.append(message)
    assert messages[0] is None                                   # one failed check could be a restart
    assert messages[1].startswith("🚨") and "does not answer" in messages[1]
    assert messages[2] is None                                   # still down: no repeat
    assert messages[3].startswith("✅") and messages[4] is None
    assert messages[5] is None and state["failures"] == 1


def test_healthcheck_run_keeps_trying_when_telegram_fails(tmp_path, monkeypatch):
    hc = load_healthcheck()
    monkeypatch.setattr(hc, "probe", lambda url: (False, "down"))
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"failures": 1, "alerted": False}))

    def broken(text):
        raise OSError("no network")

    assert hc.run("http://x", state_file, after=2, send=broken) == 1
    assert json.loads(state_file.read_text())["alerted"] is False  # so the next run sends it
    sent = []
    hc.run("http://x", state_file, after=2, send=sent.append)
    assert len(sent) == 1 and json.loads(state_file.read_text())["alerted"] is True


def test_healthcheck_probe_and_env_file(tmp_path):
    hc = load_healthcheck()
    ok, reason = hc.probe("http://127.0.0.1:9/healthz", timeout=2)   # nothing listens on port 9
    assert ok is False and "does not answer" in reason
    env = tmp_path / ".env"
    env.write_text('# c\nTELEGRAM_BOT_TOKEN="abc"\nALERTS_CHAT_ID=-100123\n\nBROKEN LINE\n')
    assert hc.read_env_file(env) == {"TELEGRAM_BOT_TOKEN": "abc", "ALERTS_CHAT_ID": "-100123"}
    assert hc.read_env_file(tmp_path / "missing") == {}


# --- dashboard password -------------------------------------------------------------------------

def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_basic_auth_accepts_only_the_password():
    assert check_basic_auth(basic("anyone", "s3cret"), "s3cret")
    assert check_basic_auth(basic("", "pa:ss"), "pa:ss")           # a colon inside the password
    for header in (None, "", "Bearer s3cret", basic("s3cret", "wrong"), "Basic !!!notbase64"):
        assert not check_basic_auth(header, "s3cret")


def test_loopback_detection():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("100.101.102.103") and not is_loopback("192.168.1.5")


# --- bot: owner admits testers, strangers are announced, flooding is refused ---------------------

@pytest.fixture
def told():
    return []


@pytest.fixture
def bot(store, cfg, told):
    return BotCore(store, cfg, build_policy(cfg, OWNER, store), owner_chat_id=OWNER, notify_owner=told.append)


def test_owner_allows_and_denies_a_tester(bot, store, told):
    assert "private" in bot.handle(FRIEND, "/start", "Ana @ana")
    assert len(told) == 1 and "Ana @ana" in told[0] and f"/allow {FRIEND}" in told[0]
    bot.handle(FRIEND, "/start", "Ana @ana")
    assert len(told) == 1                                          # the owner is told once, not on every try

    assert bot.handle(OWNER, f"/allow {FRIEND} Ana") == f"Chat {FRIEND} can now use the bot."
    assert "already allowed" in bot.handle(OWNER, f"/allow {FRIEND}")
    assert "Now watching" in bot.handle(FRIEND, "/search python")
    assert f"{FRIEND}  Ana" in bot.handle(OWNER, "/allowed")

    assert bot.handle(OWNER, f"/deny {FRIEND}") == f"Chat {FRIEND} can no longer use the bot; 1 search(es) paused."
    assert "private" in bot.handle(FRIEND, "/searches")
    assert all(not s.enabled for s in store.subscriptions_for_chat(FRIEND))
    assert "not on the list" in bot.handle(OWNER, f"/deny {FRIEND}")
    assert bot.handle(OWNER, f"/deny {OWNER}") == "That's you."
    assert bot.handle(OWNER, "/allow nobody").startswith("Which chat?")


def test_owner_commands_do_not_exist_for_anyone_else(bot, store):
    store.allow_chat(FRIEND)
    assert bot.handle(FRIEND, f"/allow {STRANGER}").startswith("I don't know that command")
    assert not store.chat_is_allowed(STRANGER)
    assert "Owner only" in bot.handle(OWNER, "/help") and "Owner only" not in bot.handle(FRIEND, "/help")


def test_stored_policy_combines_config_and_table(store):
    policy = StoredAllowlistPolicy([OWNER], store)
    assert policy.allowed(OWNER) and not policy.allowed(FRIEND)
    store.allow_chat(FRIEND)
    assert policy.allowed(FRIEND)


def test_bot_refuses_a_flood_of_commands(store, cfg):
    clock = [0.0]
    cfg = cfg.model_copy(update={"bot": cfg.bot.model_copy(update={"commands_per_minute": 3})})
    bot = BotCore(store, cfg, build_policy(cfg, OWNER, store), owner_chat_id=OWNER, clock=lambda: clock[0])
    assert all("Slow down" not in bot.handle(OWNER, "/status") for _ in range(3))
    assert "Slow down" in bot.handle(OWNER, "/status")
    assert "Slow down" not in bot.handle(FRIEND, "/status")          # limits are per chat
    clock[0] = 61.0
    assert "Slow down" not in bot.handle(OWNER, "/status")
