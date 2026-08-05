"""HTTP layer: talk to the Open-Meteo geocoding and forecast APIs.

Two endpoints are used, neither of which needs an API key:

- https://geocoding-api.open-meteo.com/v1/search   city name -> coordinates
- https://api.open-meteo.com/v1/forecast           coordinates -> weather

Everything in this module returns plain dataclasses; nothing here knows how the
data will be printed, and nothing in :mod:`display` knows about HTTP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import requests

from .cache import Cache
from .config import Config
from .errors import (
    ApiError,
    ApiTimeoutError,
    CityNotFoundError,
    NetworkError,
    ResponseParseError,
)
from .weather_codes import Condition, describe

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

USER_AGENT = "SkyQuery/1.0 (+https://github.com/yourname/skyquery)"

CURRENT_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)

DAILY_FIELDS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "sunrise",
    "sunset",
    "uv_index_max",
)

# Status codes worth retrying: transient server-side or rate-limit problems.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    """A geocoded place."""

    name: str
    latitude: float
    longitude: float
    country: str | None = None
    country_code: str | None = None
    admin1: str | None = None  # state / region / province
    timezone: str | None = None
    population: int | None = None
    elevation: float | None = None

    @property
    def label(self) -> str:
        """Human label, e.g. ``Yerevan, Yerevan, Armenia``."""
        parts = [self.name]
        if self.admin1 and self.admin1 != self.name:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)

    @property
    def coordinates(self) -> str:
        ns = "N" if self.latitude >= 0 else "S"
        ew = "E" if self.longitude >= 0 else "W"
        return f"{abs(self.latitude):.2f}°{ns}, {abs(self.longitude):.2f}°{ew}"

    @classmethod
    def from_api(cls, data: dict) -> "Location":
        try:
            return cls(
                name=str(data["name"]),
                latitude=float(data["latitude"]),
                longitude=float(data["longitude"]),
                country=_opt_str(data.get("country")),
                country_code=_opt_str(data.get("country_code")),
                admin1=_opt_str(data.get("admin1")),
                timezone=_opt_str(data.get("timezone")),
                population=_opt_int(data.get("population")),
                elevation=_opt_float(data.get("elevation")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResponseParseError(
                f"Geocoding result was missing an expected field: {exc}."
            ) from exc


@dataclass(frozen=True)
class CurrentWeather:
    """Conditions right now."""

    time: datetime | None
    temperature: float | None
    feels_like: float | None
    humidity: float | None
    precipitation: float | None
    weather_code: int | None
    cloud_cover: float | None
    pressure: float | None
    wind_speed: float | None
    wind_direction: float | None
    wind_gusts: float | None
    is_day: bool = True

    @property
    def condition(self) -> Condition:
        return describe(self.weather_code, is_day=self.is_day)

    @property
    def wind_compass(self) -> str:
        return degrees_to_compass(self.wind_direction)


@dataclass(frozen=True)
class DailyForecast:
    """One day of the forecast."""

    date: datetime | None
    weather_code: int | None
    temp_max: float | None
    temp_min: float | None
    feels_like_max: float | None
    precipitation: float | None
    precipitation_chance: float | None
    wind_max: float | None
    uv_index: float | None
    sunrise: datetime | None
    sunset: datetime | None

    @property
    def condition(self) -> Condition:
        return describe(self.weather_code, is_day=True)

    @property
    def day_name(self) -> str:
        return self.date.strftime("%a") if self.date else "?"

    @property
    def date_label(self) -> str:
        return self.date.strftime("%d %b") if self.date else "?"


@dataclass(frozen=True)
class WeatherReport:
    """Everything the display layer needs for one city."""

    location: Location
    current: CurrentWeather
    daily: list[DailyForecast] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)
    timezone: str | None = None
    utc_offset_seconds: int = 0
    from_cache: bool = False
    cache_age: float | None = None

    def unit(self, key: str, default: str = "") -> str:
        """Unit string reported by the API for ``key`` (e.g. ``temperature_2m``)."""
        return self.units.get(key, default)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class WeatherClient:
    """Fetches geocoding and forecast data, with retries and optional caching."""

    def __init__(
        self,
        config: Config | None = None,
        cache: Cache | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or Config()
        self.cache = cache
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        # Set by the last fetch_weather call so the CLI can report cache hits.
        self.last_from_cache = False

    # ---- public API ----

    def geocode(self, city: str, limit: int = 1) -> list[Location]:
        """Resolve a city name to one or more locations.

        Raises :class:`CityNotFoundError` when the API returns no candidates.
        """
        city = city.strip()
        if not city:
            raise CityNotFoundError("", hint="Provide a city name, e.g. 'skyquery Yerevan'.")

        params = {
            "name": city,
            "count": max(1, min(int(limit), 20)),
            "language": self.config.language,
            "format": "json",
        }
        payload = self._get_json(GEOCODE_URL, params, cache_key=f"geo:{self.config.language}:{city.lower()}:{params['count']}")

        results = payload.get("results")
        if not results:
            raise CityNotFoundError(
                city,
                hint="Check the spelling, or try a larger nearby city.",
            )
        if not isinstance(results, list):
            raise ResponseParseError("Geocoding response had an unexpected shape.")
        return [Location.from_api(item) for item in results if isinstance(item, dict)]

    def fetch_weather(self, location: Location, days: int | None = None) -> WeatherReport:
        """Fetch current conditions plus a daily forecast for ``location``."""
        days = days or self.config.forecast_days
        params = {
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "current": ",".join(CURRENT_FIELDS),
            "daily": ",".join(DAILY_FIELDS),
            "timezone": "auto",
            "forecast_days": days,
            "temperature_unit": self.config.temperature_unit,
            "wind_speed_unit": self.config.wind_speed_unit,
            "precipitation_unit": self.config.precipitation_unit,
        }
        cache_key = (
            f"wx:{params['latitude']},{params['longitude']}:{days}:"
            f"{self.config.units}"
        )
        payload = self._get_json(FORECAST_URL, params, cache_key=cache_key)
        from_cache = self.last_from_cache
        age = self.cache.age_of(cache_key) if (from_cache and self.cache) else None
        return self._parse_report(payload, location, from_cache=from_cache, cache_age=age)

    def get_report(self, city: str, days: int | None = None) -> WeatherReport:
        """Convenience: geocode ``city`` then fetch its weather."""
        location = self.geocode(city, limit=1)[0]
        return self.fetch_weather(location, days=days)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "WeatherClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---- internals ----

    def _get_json(self, url: str, params: dict, cache_key: str | None = None) -> dict:
        """GET ``url`` and return parsed JSON, with cache + retry handling."""
        if cache_key and self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.last_from_cache = True
                return cached

        self.last_from_cache = False
        payload = self._request_with_retries(url, params)

        if cache_key and self.cache:
            self.cache.set(cache_key, payload)
        return payload

    def _request_with_retries(self, url: str, params: dict) -> dict:
        """Perform the request, retrying transient failures with backoff."""
        attempts = self.config.retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self.session.get(url, params=params, timeout=self.config.timeout)
            except requests.exceptions.Timeout:
                last_error = ApiTimeoutError(
                    f"The weather service did not respond within {self.config.timeout:g}s.",
                    hint="Try again, or raise the timeout with --timeout.",
                )
            except requests.exceptions.SSLError as exc:
                # Not worth retrying: certificate problems are not transient.
                raise NetworkError(
                    f"Secure connection to the weather service failed: {exc}.",
                    hint="Check your system clock and any corporate proxy/TLS interception.",
                ) from exc
            except requests.exceptions.ConnectionError:
                last_error = NetworkError(
                    "Could not reach the weather service.",
                    hint="Check your internet connection and try again.",
                )
            except requests.exceptions.RequestException as exc:
                raise NetworkError(f"Request to the weather service failed: {exc}.") from exc
            else:
                error = self._error_for_status(response)
                if error is None:
                    return self._decode(response)
                if response.status_code not in RETRYABLE_STATUS:
                    raise error
                last_error = error

            if attempt < attempts - 1:
                time.sleep(_backoff_seconds(attempt))

        assert last_error is not None  # loop always sets it before falling through
        raise last_error

    @staticmethod
    def _error_for_status(response: requests.Response) -> ApiError | None:
        """Map a non-2xx response to an :class:`ApiError` (None when OK)."""
        if response.ok:
            return None

        status = response.status_code
        # Open-Meteo puts a human-readable message in the body on 4xx.
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("reason"):
                detail = f" {body['reason']}"
        except ValueError:
            pass

        if status == 429:
            return ApiError(
                f"Rate limited by the weather service (HTTP 429).{detail}",
                status_code=status,
                hint="Open-Meteo's free tier is limited; wait a minute, or rely on the cache.",
            )
        if 500 <= status < 600:
            return ApiError(
                f"The weather service reported a server error (HTTP {status}).{detail}",
                status_code=status,
                hint="This is on their side — try again shortly.",
            )
        return ApiError(
            f"The weather service rejected the request (HTTP {status}).{detail}",
            status_code=status,
        )

    @staticmethod
    def _decode(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponseParseError(
                "The weather service returned a response that was not valid JSON.",
                hint="This usually means a captive portal or proxy intercepted the request.",
            ) from exc
        if not isinstance(payload, dict):
            raise ResponseParseError("Expected a JSON object from the weather service.")
        return payload

    def _parse_report(
        self,
        payload: dict,
        location: Location,
        from_cache: bool = False,
        cache_age: float | None = None,
    ) -> WeatherReport:
        """Turn a forecast payload into a :class:`WeatherReport`."""
        current_raw = payload.get("current")
        if not isinstance(current_raw, dict):
            raise ResponseParseError(
                "The forecast response contained no current conditions.",
                hint="The API may have changed; try again or update SkyQuery.",
            )

        timezone_name = _opt_str(payload.get("timezone")) or location.timezone
        offset = _opt_int(payload.get("utc_offset_seconds")) or 0

        current = CurrentWeather(
            time=_parse_time(current_raw.get("time")),
            temperature=_opt_float(current_raw.get("temperature_2m")),
            feels_like=_opt_float(current_raw.get("apparent_temperature")),
            humidity=_opt_float(current_raw.get("relative_humidity_2m")),
            precipitation=_opt_float(current_raw.get("precipitation")),
            weather_code=_opt_int(current_raw.get("weather_code")),
            cloud_cover=_opt_float(current_raw.get("cloud_cover")),
            pressure=_opt_float(current_raw.get("pressure_msl")),
            wind_speed=_opt_float(current_raw.get("wind_speed_10m")),
            wind_direction=_opt_float(current_raw.get("wind_direction_10m")),
            wind_gusts=_opt_float(current_raw.get("wind_gusts_10m")),
            is_day=bool(_opt_int(current_raw.get("is_day"), default=1)),
        )

        units = {}
        for block in ("current_units", "daily_units"):
            values = payload.get(block)
            if isinstance(values, dict):
                units.update({k: str(v) for k, v in values.items()})

        # The location echoed by the forecast endpoint is authoritative for
        # elevation and timezone, so fold it back into what we display.
        resolved = Location(
            name=location.name,
            latitude=_opt_float(payload.get("latitude")) or location.latitude,
            longitude=_opt_float(payload.get("longitude")) or location.longitude,
            country=location.country,
            country_code=location.country_code,
            admin1=location.admin1,
            timezone=timezone_name,
            population=location.population,
            elevation=_opt_float(payload.get("elevation")) or location.elevation,
        )

        return WeatherReport(
            location=resolved,
            current=current,
            daily=_parse_daily(payload.get("daily")),
            units=units,
            timezone=timezone_name,
            utc_offset_seconds=offset,
            from_cache=from_cache,
            cache_age=cache_age,
        )


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def _parse_daily(daily_raw: Any) -> list[DailyForecast]:
    """Convert Open-Meteo's column-oriented daily block into row objects."""
    if not isinstance(daily_raw, dict):
        return []
    dates = daily_raw.get("time")
    if not isinstance(dates, list):
        return []

    def column(name: str) -> list:
        values = daily_raw.get(name)
        return values if isinstance(values, list) else []

    codes = column("weather_code")
    highs = column("temperature_2m_max")
    lows = column("temperature_2m_min")
    feels = column("apparent_temperature_max")
    precip = column("precipitation_sum")
    chance = column("precipitation_probability_max")
    wind = column("wind_speed_10m_max")
    uv = column("uv_index_max")
    sunrise = column("sunrise")
    sunset = column("sunset")

    def at(values: list, index: int):
        return values[index] if index < len(values) else None

    days: list[DailyForecast] = []
    for i, day in enumerate(dates):
        days.append(
            DailyForecast(
                date=_parse_time(day),
                weather_code=_opt_int(at(codes, i)),
                temp_max=_opt_float(at(highs, i)),
                temp_min=_opt_float(at(lows, i)),
                feels_like_max=_opt_float(at(feels, i)),
                precipitation=_opt_float(at(precip, i)),
                precipitation_chance=_opt_float(at(chance, i)),
                wind_max=_opt_float(at(wind, i)),
                uv_index=_opt_float(at(uv, i)),
                sunrise=_parse_time(at(sunrise, i)),
                sunset=_parse_time(at(sunset, i)),
            )
        )
    return days


def _parse_time(value: Any) -> datetime | None:
    """Parse Open-Meteo's ISO timestamps (``2026-08-03T14:30`` or a date)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any, default: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, capped at 4s."""
    return min(0.5 * (2 ** attempt), 4.0)


_COMPASS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def degrees_to_compass(degrees: float | None) -> str:
    """Convert a wind bearing to a 16-point compass label."""
    if degrees is None:
        return "?"
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return _COMPASS[index]


def unique_locations(locations: Iterable[Location]) -> list[Location]:
    """Drop duplicate coordinates while preserving order."""
    seen: set[tuple[float, float]] = set()
    result: list[Location] = []
    for location in locations:
        key = (round(location.latitude, 3), round(location.longitude, 3))
        if key in seen:
            continue
        seen.add(key)
        result.append(location)
    return result
