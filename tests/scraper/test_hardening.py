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


# --- backups --------------------------------------------------------------------------------------

def make_dump(directory, name, age_days, now=NOW):
    import os
    path = directory / name
    path.write_bytes(b"x")
    os.utime(path, (now - age_days * 86400,) * 2)
    return path


def test_prune_deletes_old_dumps_but_keeps_the_newest_three(tmp_path):
    from app.scraper.backup import list_dumps, prune

    for i, age in enumerate([40, 30, 20, 10, 1]):
        make_dump(tmp_path, f"upwork-{i}.dump", age)
    make_dump(tmp_path, "notes.txt", 99)
    make_dump(tmp_path, "upwork-x.part", 99)                         # an interrupted run is not a backup
    assert [p.name for p in prune(tmp_path, keep_days=14, now=NOW)] == ["upwork-0.dump", "upwork-1.dump"]
    assert [p.name for p in list_dumps(tmp_path)] == ["upwork-2.dump", "upwork-3.dump", "upwork-4.dump"]
    assert prune(tmp_path, keep_days=0.5, now=NOW) == []             # never below three, however old
    assert (tmp_path / "notes.txt").exists()


def test_backup_dumps_verifies_copies_and_cleans_up_on_failure(tmp_path, cfg):
    import subprocess
    from app.scraper.backup import list_dumps, run_backup

    backup = cfg.backup.model_copy(update={"dir": str(tmp_path / "local"), "copy_to": str(tmp_path / "drive")})
    env = {"DB_NAME": "db", "DB_USER": "u", "DB_PASSWORD": "secret", "DB_HOST": "h", "DB_PORT": "5433"}
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "pg_dump":
            Path(cmd[cmd.index("-f") + 1]).write_bytes(b"dump")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = run_backup(backup, env, run=fake_run, now=datetime(2026, 10, 2, 3, 0, 0))
    dump_cmd, dump_kwargs = calls[0]
    assert dump_cmd[:7] == ["pg_dump", "-h", "h", "-p", "5433", "-U", "u"] and dump_cmd[-1] == "db"
    assert dump_kwargs["env"]["PGPASSWORD"] == "secret" and "secret" not in " ".join(dump_cmd)   # never on the command line
    assert calls[1][0][:2] == ["pg_restore", "--list"]
    assert Path(result["file"]).name == "upwork-20261002-030000.dump" and result["bytes"] == 4
    assert (tmp_path / "drive" / "upwork-20261002-030000.dump").read_bytes() == b"dump"

    def failing_run(cmd, **kwargs):
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"half")
        raise subprocess.CalledProcessError(1, cmd, stderr="connection refused")

    with pytest.raises(subprocess.CalledProcessError):
        run_backup(backup, env, run=failing_run, now=datetime(2026, 10, 3, 3, 0, 0))
    assert len(list_dumps(tmp_path / "local")) == 1 and not list((tmp_path / "local").glob("*.part"))


@pytest.mark.parametrize("checked, age_h, expected", [
    (False, None, []),                    # backups disabled, or the folder could not be read
    (True, None, ["backup_missing"]),
    (True, 5, []),
    (True, 31, ["backup_stale"]),
])
def test_backup_health_rule(cfg, checked, age_h, expected):
    from app.scraper.health import evaluate

    snap = HealthSnapshot(at=NOW, roles=[RoleBeat(r, r, NOW) for r in ("scheduler", "fetcher", "dispatcher")],
                          backup_checked=checked, backup_age_s=None if age_h is None else age_h * 3600)
    assert [p.key for p in evaluate(snap, cfg)] == expected


# --- log rotation ---------------------------------------------------------------------------------

def test_log_rotation_keeps_a_fixed_number_of_files(tmp_path):
    from app.scraper.cli import rotate_log

    log_file = tmp_path / "scraper.log"
    assert rotate_log(log_file, max_bytes=10, keep=2) is False       # nothing there yet
    for generation in ("first", "second", "third"):
        log_file.write_text(generation * 5)
        assert rotate_log(log_file, max_bytes=10, keep=2) is True
    assert not log_file.exists()
    assert (tmp_path / "scraper.log.1").read_text() == "third" * 5
    assert (tmp_path / "scraper.log.2").read_text() == "second" * 5
    assert not (tmp_path / "scraper.log.3").exists()                 # "first" was dropped
    log_file.write_text("small")
    assert rotate_log(log_file, max_bytes=10, keep=2) is False and log_file.exists()
