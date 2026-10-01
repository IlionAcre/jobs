from __future__ import annotations

import copy

import pytest
import yaml

from app.scraper.config import ScraperConfig, load_scraper_config
from app.scraper.errors import ConfigError


def test_shipped_yaml_is_valid():
    cfg = load_scraper_config()
    assert cfg.poller.transport in cfg.transports
    assert cfg.minters[0].name == "curl_cffi"  # the cheapest minter must stay first
    assert cfg.tokens.refresh_after_hours < cfg.tokens.max_age_hours


def test_minimal_dict_is_valid(config_dict):
    cfg = ScraperConfig.model_validate(config_dict)
    assert cfg.egress_id == "default"
    assert cfg.transports["curl_cffi"].impersonate == "chrome150"


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda c: c["poller"].update(typo_key=1), "typo_key"),
        (lambda c: c["poller"].update(transport="nope"), "poller.transport"),
        (lambda c: c["minters"].append({"name": "x", "kind": "http", "transport": "nope"}), "not defined under transports"),
        (lambda c: c["minters"].append({"name": "curl_cffi", "kind": "http", "transport": "curl_cffi"}), "unique"),
        (lambda c: c["minters"].append({"name": "b", "kind": "subprocess"}), "needs `python` and `script`"),
        (lambda c: c["tokens"].update(refresh_after_hours=20), "smaller than max_age_hours"),
        (lambda c: c["transports"]["curl_cffi"].pop("impersonate"), "needs `impersonate`"),
        (lambda c: c["poller"].update(details_count=20), "cannot exceed"),
        (lambda c: c["poller"].update(ids_count=51, details_count=5), "ids_count"),
        (lambda c: c.update(minters=[]), "minters"),
    ],
)
def test_invalid_configs_are_rejected(config_dict, tmp_path, mutate, fragment):
    bad = copy.deepcopy(config_dict)
    mutate(bad)
    path = tmp_path / "scraper.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_scraper_config(path)
    assert fragment in str(err.value)


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_scraper_config(tmp_path / "absent.yaml")


def test_env_var_selects_the_file(config_dict, tmp_path, monkeypatch):
    path = tmp_path / "alt.yaml"
    config_dict["retention_days"] = 7
    path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")
    monkeypatch.setenv("SCRAPER_CONFIG_PATH", str(path))
    assert load_scraper_config().retention_days == 7
