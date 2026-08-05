"""Shared fixtures: a fake Open-Meteo payload and an isolated home directory.

No test in this suite touches the network — the HTTP layer is exercised through
a stub session that returns canned responses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the project root importable so `from src... import ...` works when
# pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api_client import Location, WeatherClient  # noqa: E402
from src.config import Config  # noqa: E402


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, payload=None, status_code: int = 200, text: str | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON object could be decoded")
        return self._payload


class FakeSession:
    """Replays a queue of responses (or exceptions) and records the requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError(f"Unexpected extra request to {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


@pytest.fixture
def geocode_payload() -> dict:
    return {
        "results": [
            {
                "id": 616052,
                "name": "Yerevan",
                "latitude": 40.18111,
                "longitude": 44.51361,
                "elevation": 1023.0,
                "country": "Armenia",
                "country_code": "AM",
                "admin1": "Yerevan",
                "timezone": "Asia/Yerevan",
                "population": 1093485,
            }
        ]
    }


@pytest.fixture
def forecast_payload() -> dict:
    return {
        "latitude": 40.1875,
        "longitude": 44.5,
        "elevation": 1030.0,
        "timezone": "Asia/Yerevan",
        "utc_offset_seconds": 14400,
        "current_units": {
            "temperature_2m": "°C",
            "wind_speed_10m": "km/h",
            "precipitation": "mm",
        },
        "current": {
            "time": "2026-08-03T14:30",
            "temperature_2m": 31.4,
            "relative_humidity_2m": 22,
            "apparent_temperature": 29.8,
            "is_day": 1,
            "precipitation": 0.0,
            "weather_code": 1,
            "cloud_cover": 12,
            "pressure_msl": 1008.4,
            "wind_speed_10m": 9.7,
            "wind_direction_10m": 315,
            "wind_gusts_10m": 18.4,
        },
        "daily_units": {"temperature_2m_max": "°C"},
        "daily": {
            "time": ["2026-08-03", "2026-08-04", "2026-08-05"],
            "weather_code": [1, 95, 3],
            "temperature_2m_max": [34.2, 30.1, 28.7],
            "temperature_2m_min": [19.8, 18.2, 17.5],
            "apparent_temperature_max": [32.0, 29.0, 27.5],
            "precipitation_sum": [0.0, 6.4, 1.2],
            "precipitation_probability_max": [0, 70, 25],
            "wind_speed_10m_max": [14.2, 22.5, 11.0],
            "uv_index_max": [8.5, 6.2, 7.0],
            "sunrise": ["2026-08-03T06:12", "2026-08-04T06:13", "2026-08-05T06:14"],
            "sunset": ["2026-08-03T20:31", "2026-08-04T20:30", "2026-08-05T20:29"],
        },
    }


@pytest.fixture
def location() -> Location:
    return Location(
        name="Yerevan",
        latitude=40.18111,
        longitude=44.51361,
        country="Armenia",
        country_code="AM",
        admin1="Yerevan",
        timezone="Asia/Yerevan",
        population=1093485,
        elevation=1023.0,
    )


@pytest.fixture
def make_client():
    """Build a WeatherClient backed by a scripted FakeSession."""

    def _make(responses, config: Config | None = None, cache=None) -> WeatherClient:
        session = FakeSession(responses)
        return WeatherClient(config=config or Config(), cache=cache, session=session)

    return _make


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point config/cache at a temp directory so tests never touch ~/.skyquery."""
    monkeypatch.setenv("SKYQUERY_HOME", str(tmp_path / "skyquery-home"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    return tmp_path / "skyquery-home"
