"""Tests for the rendering layer — no network, no config files."""

from __future__ import annotations

import io
import json

import pytest

from src.api_client import CurrentWeather, DailyForecast, Location, WeatherReport
from src.display import (
    Theme,
    display_width,
    pad,
    pad_styled,
    pretty_unit,
    render_error,
    render_location_choices,
    render_report,
    render_summary_table,
    report_to_dict,
    should_use_color,
    supports_unicode,
    truncate,
)
from src.errors import CityNotFoundError
from src.weather_codes import describe, is_severe


@pytest.fixture
def report(location) -> WeatherReport:
    current = CurrentWeather(
        time=None,
        temperature=31.4,
        feels_like=29.8,
        humidity=22,
        precipitation=0.0,
        weather_code=1,
        cloud_cover=12,
        pressure=1008.4,
        wind_speed=9.7,
        wind_direction=315,
        wind_gusts=18.4,
        is_day=True,
    )
    daily = [
        DailyForecast(
            date=None,
            weather_code=code,
            temp_max=high,
            temp_min=low,
            feels_like_max=high - 2,
            precipitation=1.0,
            precipitation_chance=chance,
            wind_max=14.0,
            uv_index=7.0,
            sunrise=None,
            sunset=None,
        )
        for code, high, low, chance in [(1, 34.2, 19.8, 0), (95, 30.1, 18.2, 70)]
    ]
    return WeatherReport(
        location=location,
        current=current,
        daily=daily,
        units={"temperature_2m": "°C", "wind_speed_10m": "km/h", "precipitation": "mm"},
        timezone="Asia/Yerevan",
    )


# ------------------------------------------------------------------- themes


def test_theme_adds_escapes_only_when_enabled():
    assert Theme(enabled=True).bold("hi") == "\033[1mhi\033[0m"
    assert Theme(enabled=False).bold("hi") == "hi"


def test_temperature_colouring_differs_between_cold_and_hot():
    theme = Theme(enabled=True)
    assert theme.temperature(-5, "x") != theme.temperature(35, "x")
    assert theme.temperature(None, "x") == "x"


def test_ascii_icons_are_used_when_unicode_is_unavailable():
    condition = describe(0)
    assert Theme(unicode_icons=True).icon(condition) == condition.icon
    assert Theme(unicode_icons=False).icon(condition) == condition.ascii_icon


# ------------------------------------------------------- capability probing


def test_color_modes_are_honoured():
    assert should_use_color("never", io.StringIO()) is False
    assert should_use_color("always", io.StringIO()) is True
    assert should_use_color("auto", io.StringIO()) is False  # not a tty


def test_no_color_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert should_use_color("auto") is False


def test_supports_unicode_detects_a_legacy_code_page():
    class AsciiStream(io.StringIO):
        encoding = "cp437"

    class Utf8Stream(io.StringIO):
        encoding = "utf-8"

    assert supports_unicode(AsciiStream()) is False
    assert supports_unicode(Utf8Stream()) is True


# ------------------------------------------------------------ width helpers


def test_display_width_counts_emoji_as_two_cells():
    assert display_width("abc") == 3
    assert display_width("\N{CLOUD WITH RAIN}") == 2
    assert display_width("á") == 1  # combining accent adds no width


def test_pad_aligns_to_the_requested_width():
    assert pad("ab", 5) == "ab   "
    assert pad("ab", 5, "right") == "   ab"
    assert pad("ab", 6, "center") == "  ab  "


def test_pad_truncates_when_the_text_is_too_long():
    assert display_width(pad("abcdefgh", 4)) == 4


def test_truncate_adds_an_ellipsis():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 10) == "abc"


def test_pad_styled_ignores_escape_codes_when_measuring():
    styled = Theme(enabled=True).red("21°C")
    padded = pad_styled(styled, "21°C", 10, "right")
    assert padded == "      " + styled
    assert display_width(padded.replace("\033[31m", "").replace("\033[0m", "")) == 10


def test_pad_styled_drops_styling_rather_than_cutting_an_escape():
    styled = Theme(enabled=True).red("21°C")
    assert "\033" not in pad_styled(styled, "21°C", 2)


def test_api_unit_labels_are_tidied():
    assert pretty_unit("mp/h") == "mph"
    assert pretty_unit("km/h") == "km/h"
    assert pretty_unit("°C") == "°C"


# ------------------------------------------------------------------ panels


def test_report_renders_the_key_facts(report):
    output = render_report(report, theme=Theme(enabled=False, unicode_icons=False))
    assert "Yerevan, Armenia" in output
    assert "31.4°C" in output
    assert "Feels like 29.8°C" in output
    assert "Mainly clear" in output
    assert "Humidity" in output
    assert "Thunderstorm" in output  # from the forecast rows


def test_box_borders_stay_aligned_without_color(report):
    output = render_report(report, theme=Theme(enabled=False, unicode_icons=True), width=60)
    box_lines = [line for line in output.split("\n") if line.startswith(("┌", "│", "├", "└"))]
    widths = {display_width(line) for line in box_lines}
    assert widths == {60}, f"misaligned box rows: {widths}"


def test_colored_output_keeps_the_same_printed_width(report):
    plain = render_report(report, theme=Theme(enabled=False, unicode_icons=True), width=60)
    colored = render_report(report, theme=Theme(enabled=True, unicode_icons=True), width=60)
    assert len(colored) > len(plain)  # escapes were added
    assert colored.count("\n") == plain.count("\n")


def test_forecast_can_be_omitted(report):
    output = render_report(report, theme=Theme(enabled=False), show_forecast=False)
    assert "forecast" not in output.lower()


def test_missing_values_render_as_a_dash(location):
    empty = CurrentWeather(*(None,) * 11)
    report = WeatherReport(location=location, current=empty)
    output = render_report(report, theme=Theme(enabled=False, unicode_icons=False))
    assert "—" in output
    assert "Unknown conditions" in output


def test_cache_footer_is_shown_for_cached_reports(report):
    cached = WeatherReport(
        location=report.location,
        current=report.current,
        daily=report.daily,
        units=report.units,
        from_cache=True,
        cache_age=125.0,
    )
    assert "cache" in render_report(cached, theme=Theme(enabled=False)).lower()
    assert "2m old" in render_report(cached, theme=Theme(enabled=False))


def test_summary_table_has_one_row_per_city(report):
    table = render_summary_table([report, report], theme=Theme(enabled=False, unicode_icons=False))
    lines = table.split("\n")
    assert lines[0].startswith("CITY")
    assert sum(1 for line in lines if line.startswith("Yerevan")) == 2


def test_summary_table_of_nothing_is_empty():
    assert render_summary_table([]) == ""


def test_location_choices_are_numbered(location):
    other = Location(name="Yerevan", latitude=1.0, longitude=2.0, country="Elsewhere")
    output = render_location_choices([location, other], theme=Theme(enabled=False))
    assert "1. Yerevan, Armenia" in output
    assert "2. Yerevan, Elsewhere" in output


def test_error_rendering_includes_the_hint():
    output = render_error(CityNotFoundError("Xyzzy", hint="Check the spelling."),
                          theme=Theme(enabled=False))
    assert "Xyzzy" in output
    assert "Hint: Check the spelling." in output


# -------------------------------------------------------------- json output


def test_report_to_dict_is_json_serialisable(report):
    payload = report_to_dict(report)
    text = json.dumps(payload)  # must not raise

    assert json.loads(text)["current"]["temperature"] == 31.4
    assert payload["location"]["name"] == "Yerevan"
    assert payload["current"]["wind_compass"] == "NW"
    assert len(payload["daily"]) == 2


# ---------------------------------------------------------- weather codes


def test_weather_codes_decode_to_descriptions():
    assert describe(0).description == "Clear sky"
    assert describe(95).description == "Thunderstorm"
    assert describe(None).description == "Unknown conditions"
    assert describe(1234).description == "Unknown conditions"
    assert describe("61").description == "Slight rain"  # numeric strings are fine


def test_night_icons_differ_from_day_icons():
    assert describe(0, is_day=False).icon != describe(0, is_day=True).icon
    assert describe(0, is_day=False).description == "Clear sky"


def test_severe_conditions_are_flagged():
    assert is_severe(95) is True
    assert is_severe(1) is False
