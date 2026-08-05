"""Tests for configuration defaults, validation and persistence."""

from __future__ import annotations

import json

import pytest

from src.config import Config, default_config_dir
from src.errors import ConfigError


def test_defaults_are_metric():
    config = Config()
    assert config.units == "metric"
    assert config.temperature_unit == "celsius"
    assert config.wind_speed_unit == "kmh"
    assert config.precipitation_unit == "mm"
    assert config.temperature_symbol == "°C"


def test_imperial_maps_to_the_api_unit_names():
    config = Config(units="imperial")
    assert config.temperature_unit == "fahrenheit"
    assert config.wind_speed_unit == "mph"
    assert config.precipitation_unit == "inch"
    assert config.temperature_symbol == "°F"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"units": "kelvin"},
        {"color": "rainbow"},
        {"forecast_days": 0},
        {"forecast_days": 99},
        {"timeout": 0},
        {"retries": -1},
        {"cache_ttl": -5},
    ],
)
def test_invalid_values_are_rejected(kwargs):
    with pytest.raises(ConfigError):
        Config(**kwargs).validate()


def test_missing_config_file_falls_back_to_defaults(tmp_path):
    assert Config.load(tmp_path / "absent.json") == Config()


def test_empty_config_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("   \n", encoding="utf-8")
    assert Config.load(path) == Config()


def test_config_round_trips_through_disk(tmp_path):
    path = tmp_path / "config.json"
    Config(units="imperial", forecast_days=7, cache_ttl=1200).save(path)

    loaded = Config.load(path)
    assert loaded.units == "imperial"
    assert loaded.forecast_days == 7
    assert loaded.cache_ttl == 1200


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "config.json"
    assert Config().save(path) == path
    assert path.exists()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"units": "imperial", "future_option": True}), encoding="utf-8")
    assert Config.load(path).units == "imperial"


def test_malformed_json_reports_a_config_error(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{units: imperial}", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        Config.load(path)
    assert excinfo.value.hint


def test_non_object_json_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_wrongly_typed_value_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"forecast_days": "seven"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_invalid_stored_value_is_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"units": "kelvin"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_config_dir_follows_the_environment_override(isolated_home):
    assert default_config_dir() == isolated_home
