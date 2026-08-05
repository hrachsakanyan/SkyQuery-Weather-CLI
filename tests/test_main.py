"""End-to-end tests for the CLI, with the HTTP session stubbed out."""

from __future__ import annotations

import json

import pytest

from src.main import EXIT_ERROR, EXIT_OK, EXIT_USAGE, build_parser, main, resolve_config
from src.config import Config
from src.errors import ConfigError

from .conftest import FakeResponse, FakeSession


@pytest.fixture
def cli(monkeypatch, geocode_payload, forecast_payload):
    """Run ``main`` with a scripted session; returns the exit code."""

    def _run(argv, responses=None):
        if responses is None:
            responses = [FakeResponse(geocode_payload), FakeResponse(forecast_payload)]
        session = FakeSession(responses)
        monkeypatch.setattr("src.api_client.requests.Session", lambda: session)
        return main(argv)

    return _run


# ----------------------------------------------------------- argument parsing


def test_parser_accepts_multiple_cities():
    args = build_parser().parse_args(["Paris", "Tokyo", "--short"])
    assert args.cities == ["Paris", "Tokyo"]
    assert args.short is True


def test_cli_flags_override_the_stored_config():
    args = build_parser().parse_args(["Paris", "--units", "imperial", "--days", "3"])
    config = resolve_config(args)
    assert config.units == "imperial"
    assert config.forecast_days == 3


def test_no_color_flag_wins_over_color_flag():
    args = build_parser().parse_args(["Paris", "--color", "always", "--no-color"])
    assert resolve_config(args).color == "never"


def test_out_of_range_days_is_rejected():
    args = build_parser().parse_args(["Paris", "--days", "40"])
    with pytest.raises(ConfigError):
        resolve_config(args)


def test_invalid_days_exits_with_an_error(cli, capsys):
    assert cli(["Paris", "--days", "40"], responses=[]) == EXIT_ERROR
    assert "forecast_days" in capsys.readouterr().err


# ------------------------------------------------------------------ queries


def test_single_city_prints_a_report(cli, capsys):
    assert cli(["Yerevan", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Yerevan, Armenia" in out
    assert "31.4" in out


def test_json_output_is_valid_json(cli, capsys):
    assert cli(["Yerevan", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["location"]["name"] == "Yerevan"
    assert payload["current"]["condition"] == "Mainly clear"


def test_multiple_cities_emit_a_json_array(cli, capsys, geocode_payload, forecast_payload):
    responses = [FakeResponse(geocode_payload), FakeResponse(forecast_payload)] * 2
    assert cli(["Yerevan", "Yerevan", "--json", "--no-cache"], responses) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_short_mode_prints_the_summary_table(cli, capsys):
    assert cli(["Yerevan", "--short", "--no-color"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("CITY")
    assert "Yerevan" in out


def test_no_forecast_hides_the_forecast_section(cli, capsys):
    assert cli(["Yerevan", "--no-forecast", "--no-color"]) == EXIT_OK
    assert "forecast" not in capsys.readouterr().out.lower()


def test_list_mode_shows_matches_without_fetching_weather(cli, capsys, geocode_payload):
    assert cli(["Yerevan", "--list"], [FakeResponse(geocode_payload)]) == EXIT_OK
    assert "Matches for 'Yerevan'" in capsys.readouterr().out


def test_unknown_city_reports_an_error(cli, capsys):
    assert cli(["Xyzzyville"], [FakeResponse({})]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "No location found" in err
    assert "Hint:" in err


def test_one_bad_city_still_prints_the_good_ones(
    cli, capsys, geocode_payload, forecast_payload
):
    responses = [
        FakeResponse(geocode_payload),
        FakeResponse(forecast_payload),
        FakeResponse({}),  # second city: no geocoding results
    ]
    assert cli(["Yerevan", "Xyzzyville", "--no-color"], responses) == EXIT_ERROR
    captured = capsys.readouterr()
    assert "Yerevan, Armenia" in captured.out
    assert "Xyzzyville" in captured.err


def test_server_error_is_reported_without_a_traceback(cli, capsys):
    responses = [FakeResponse({"reason": "boom"}, status_code=400)]
    assert cli(["Yerevan"], responses) == EXIT_ERROR
    assert "HTTP 400" in capsys.readouterr().err


def test_no_arguments_prints_help(cli, capsys):
    assert cli([], responses=[]) == EXIT_USAGE
    assert "usage: skyquery" in capsys.readouterr().out


# ------------------------------------------------------- config subcommands


def test_show_config_lists_the_settings(cli, capsys):
    assert cli(["--show-config"], responses=[]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Config file:" in out
    assert "units" in out


def test_set_units_persists_the_choice(cli, capsys, isolated_home):
    assert cli(["--set-units", "imperial"], responses=[]) == EXIT_OK
    assert "imperial" in capsys.readouterr().out
    assert Config.load(isolated_home / "config.json").units == "imperial"


def test_set_days_persists_the_choice(cli, isolated_home):
    assert cli(["--set-days", "9"], responses=[]) == EXIT_OK
    assert Config.load(isolated_home / "config.json").forecast_days == 9


def test_saved_config_is_used_by_later_runs(cli, capsys, isolated_home):
    cli(["--set-units", "imperial"], responses=[])
    capsys.readouterr()

    args = build_parser().parse_args(["Yerevan"])
    assert resolve_config(args).units == "imperial"


def test_clear_cache_reports_what_it_removed(cli, capsys):
    cli(["Yerevan"])  # populates the cache
    capsys.readouterr()
    assert cli(["--clear-cache"], responses=[]) == EXIT_OK
    assert "Cleared 2 cached entries" in capsys.readouterr().out


def test_repeated_queries_reuse_the_cache(cli, capsys, geocode_payload, forecast_payload):
    session_responses = [FakeResponse(geocode_payload), FakeResponse(forecast_payload)]
    assert cli(["Yerevan", "--no-color"], session_responses) == EXIT_OK
    capsys.readouterr()

    # No responses queued: any network call would raise inside FakeSession.
    assert cli(["Yerevan", "--no-color"], []) == EXIT_OK
    assert "cache" in capsys.readouterr().out.lower()
