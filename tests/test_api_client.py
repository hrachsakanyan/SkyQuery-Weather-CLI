"""Tests for the HTTP layer: parsing, error mapping, retries and caching."""

from __future__ import annotations

import requests

import pytest

from src.api_client import (
    WeatherClient,
    degrees_to_compass,
    unique_locations,
)
from src.cache import Cache
from src.config import Config
from src.errors import (
    ApiError,
    ApiTimeoutError,
    CityNotFoundError,
    NetworkError,
    ResponseParseError,
)

from .conftest import FakeResponse


# ---------------------------------------------------------------- geocoding


def test_geocode_returns_parsed_location(make_client, geocode_payload):
    client = make_client([FakeResponse(geocode_payload)])
    location = client.geocode("Yerevan")[0]

    assert location.name == "Yerevan"
    assert location.country == "Armenia"
    assert location.latitude == pytest.approx(40.18111)
    assert location.label == "Yerevan, Armenia"  # admin1 == name, so it is dropped
    assert "40.18°N" in location.coordinates


def test_geocode_sends_the_query_parameters(make_client, geocode_payload):
    client = make_client([FakeResponse(geocode_payload)])
    client.geocode("Yerevan", limit=5)

    url, params = client.session.calls[0]
    assert "geocoding-api.open-meteo.com" in url
    assert params["name"] == "Yerevan"
    assert params["count"] == 5


def test_geocode_raises_when_there_are_no_results(make_client):
    client = make_client([FakeResponse({"generationtime_ms": 0.1})])
    with pytest.raises(CityNotFoundError) as excinfo:
        client.geocode("Xyzzyville")
    assert "Xyzzyville" in str(excinfo.value)
    assert excinfo.value.hint  # the user gets something actionable


def test_geocode_rejects_an_empty_city_name(make_client):
    client = make_client([])
    with pytest.raises(CityNotFoundError):
        client.geocode("   ")


# ----------------------------------------------------------------- forecast


def test_fetch_weather_parses_current_conditions(make_client, forecast_payload, location):
    client = make_client([FakeResponse(forecast_payload)])
    report = client.fetch_weather(location)

    assert report.current.temperature == pytest.approx(31.4)
    assert report.current.feels_like == pytest.approx(29.8)
    assert report.current.is_day is True
    assert report.current.condition.description == "Mainly clear"
    assert report.current.wind_compass == "NW"
    assert report.unit("temperature_2m") == "°C"
    assert report.timezone == "Asia/Yerevan"


def test_fetch_weather_parses_the_daily_columns_into_rows(make_client, forecast_payload, location):
    client = make_client([FakeResponse(forecast_payload)])
    report = client.fetch_weather(location)

    assert len(report.daily) == 3
    stormy = report.daily[1]
    assert stormy.condition.description == "Thunderstorm"
    assert stormy.temp_max == pytest.approx(30.1)
    assert stormy.precipitation_chance == pytest.approx(70)
    assert stormy.sunrise is not None and stormy.sunrise.hour == 6


def test_fetch_weather_survives_a_short_daily_column(make_client, forecast_payload, location):
    """A truncated column must not raise — missing values become None."""
    forecast_payload["daily"]["temperature_2m_min"] = [19.8]
    client = make_client([FakeResponse(forecast_payload)])
    report = client.fetch_weather(location)

    assert report.daily[0].temp_min == pytest.approx(19.8)
    assert report.daily[2].temp_min is None


def test_fetch_weather_without_a_daily_block(make_client, forecast_payload, location):
    del forecast_payload["daily"]
    client = make_client([FakeResponse(forecast_payload)])
    assert client.fetch_weather(location).daily == []


def test_fetch_weather_requires_current_conditions(make_client, forecast_payload, location):
    del forecast_payload["current"]
    client = make_client([FakeResponse(forecast_payload)])
    with pytest.raises(ResponseParseError):
        client.fetch_weather(location)


def test_unit_selection_is_sent_to_the_api(make_client, forecast_payload, location):
    client = make_client([FakeResponse(forecast_payload)], config=Config(units="imperial"))
    client.fetch_weather(location, days=7)

    _, params = client.session.calls[0]
    assert params["temperature_unit"] == "fahrenheit"
    assert params["wind_speed_unit"] == "mph"
    assert params["precipitation_unit"] == "inch"
    assert params["forecast_days"] == 7


def test_get_report_chains_geocoding_and_forecast(make_client, geocode_payload, forecast_payload):
    client = make_client([FakeResponse(geocode_payload), FakeResponse(forecast_payload)])
    report = client.get_report("Yerevan")

    assert report.location.name == "Yerevan"
    assert report.current.temperature == pytest.approx(31.4)
    assert len(client.session.calls) == 2


# ------------------------------------------------------------ error mapping


def test_timeout_is_reported_as_a_timeout_error(make_client):
    client = make_client(
        [requests.exceptions.Timeout(), requests.exceptions.Timeout()],
        config=Config(retries=1),
    )
    with pytest.raises(ApiTimeoutError):
        client.geocode("Yerevan")


def test_connection_failure_is_reported_as_a_network_error(make_client):
    client = make_client([requests.exceptions.ConnectionError()], config=Config(retries=0))
    with pytest.raises(NetworkError):
        client.geocode("Yerevan")


def test_ssl_errors_are_not_retried(make_client):
    client = make_client([requests.exceptions.SSLError("bad cert")], config=Config(retries=3))
    with pytest.raises(NetworkError):
        client.geocode("Yerevan")
    assert len(client.session.calls) == 1


def test_client_error_status_is_surfaced_with_the_api_reason(make_client):
    client = make_client([FakeResponse({"reason": "Cannot initialize"}, status_code=400)])
    with pytest.raises(ApiError) as excinfo:
        client.geocode("Yerevan")
    assert excinfo.value.status_code == 400
    assert "Cannot initialize" in str(excinfo.value)


def test_rate_limiting_carries_a_hint(make_client):
    client = make_client([FakeResponse({}, status_code=429)] * 2, config=Config(retries=1))
    with pytest.raises(ApiError) as excinfo:
        client.geocode("Yerevan")
    assert excinfo.value.status_code == 429
    assert "Rate limited" in str(excinfo.value)


def test_non_json_response_is_a_parse_error(make_client):
    client = make_client([FakeResponse(None, text="<html>captive portal</html>")])
    with pytest.raises(ResponseParseError):
        client.geocode("Yerevan")


# ---------------------------------------------------------------- retrying


def test_server_errors_are_retried_then_succeed(make_client, geocode_payload, monkeypatch):
    monkeypatch.setattr("src.api_client.time.sleep", lambda _: None)
    client = make_client(
        [FakeResponse({}, status_code=503), FakeResponse(geocode_payload)],
        config=Config(retries=2),
    )
    assert client.geocode("Yerevan")[0].name == "Yerevan"
    assert len(client.session.calls) == 2


def test_retries_give_up_after_the_configured_attempts(make_client, monkeypatch):
    monkeypatch.setattr("src.api_client.time.sleep", lambda _: None)
    client = make_client([FakeResponse({}, status_code=500)] * 3, config=Config(retries=2))
    with pytest.raises(ApiError):
        client.geocode("Yerevan")
    assert len(client.session.calls) == 3  # 1 attempt + 2 retries


def test_bad_request_is_not_retried(make_client):
    client = make_client([FakeResponse({}, status_code=400)], config=Config(retries=3))
    with pytest.raises(ApiError):
        client.geocode("Yerevan")
    assert len(client.session.calls) == 1


# ------------------------------------------------------------------ caching


def test_second_lookup_is_served_from_the_cache(tmp_path, make_client, forecast_payload, location):
    cache = Cache(tmp_path / "cache", ttl=600)
    client = make_client([FakeResponse(forecast_payload)], cache=cache)
    first = client.fetch_weather(location)

    second = client.fetch_weather(location)  # no queued response left: must be cached
    assert len(client.session.calls) == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.current.temperature == first.current.temperature


def test_disabled_cache_always_hits_the_network(tmp_path, make_client, forecast_payload, location):
    cache = Cache(tmp_path / "cache", ttl=600, enabled=False)
    client = make_client([FakeResponse(forecast_payload)] * 2, cache=cache)
    client.fetch_weather(location)
    client.fetch_weather(location)
    assert len(client.session.calls) == 2


def test_different_units_do_not_share_a_cache_entry(tmp_path, make_client, forecast_payload, location):
    cache = Cache(tmp_path / "cache", ttl=600)
    metric = make_client([FakeResponse(forecast_payload)], cache=cache)
    metric.fetch_weather(location)

    imperial = make_client(
        [FakeResponse(forecast_payload)], config=Config(units="imperial"), cache=cache
    )
    imperial.fetch_weather(location)
    assert len(imperial.session.calls) == 1


# ------------------------------------------------------------------ helpers


@pytest.mark.parametrize(
    "degrees,expected",
    [(0, "N"), (45, "NE"), (90, "E"), (180, "S"), (270, "W"), (350, "N"), (None, "?")],
)
def test_degrees_to_compass(degrees, expected):
    assert degrees_to_compass(degrees) == expected


def test_unique_locations_drops_duplicate_coordinates(location):
    duplicate = location  # same coordinates
    other = type(location)(name="Gyumri", latitude=40.79, longitude=43.84)
    assert len(unique_locations([location, duplicate, other])) == 2


def test_client_works_as_a_context_manager(make_client, geocode_payload):
    with make_client([FakeResponse(geocode_payload)]) as client:
        assert client.geocode("Yerevan")


def test_default_client_creates_its_own_session():
    client = WeatherClient()
    assert client.session.headers["User-Agent"].startswith("SkyQuery/")
    client.close()
