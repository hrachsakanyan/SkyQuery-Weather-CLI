"""User configuration for SkyQuery.

Settings resolve in this order (last one wins):

1. built-in defaults
2. ``~/.skyquery/config.json``
3. CLI flags (applied by ``main``)

The file is optional; a missing or empty config is not an error.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .errors import ConfigError

UNIT_SYSTEMS = ("metric", "imperial")
COLOR_MODES = ("auto", "always", "never")

MIN_FORECAST_DAYS = 1
MAX_FORECAST_DAYS = 16  # Open-Meteo's documented ceiling


def default_config_dir() -> Path:
    """Directory holding the config file and the response cache.

    ``SKYQUERY_HOME`` overrides it, which is what the tests use to stay out of
    the real home directory.
    """
    override = os.environ.get("SKYQUERY_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".skyquery"


@dataclass
class Config:
    """Runtime settings for a single CLI invocation."""

    units: str = "metric"
    forecast_days: int = 5
    timeout: float = 10.0
    retries: int = 2
    cache_enabled: bool = True
    cache_ttl: int = 600  # seconds
    color: str = "auto"
    language: str = "en"

    # ---- derived values the API client and display layer ask for ----

    @property
    def temperature_unit(self) -> str:
        return "celsius" if self.units == "metric" else "fahrenheit"

    @property
    def wind_speed_unit(self) -> str:
        return "kmh" if self.units == "metric" else "mph"

    @property
    def precipitation_unit(self) -> str:
        return "mm" if self.units == "metric" else "inch"

    @property
    def temperature_symbol(self) -> str:
        return "°C" if self.units == "metric" else "°F"

    # ---- validation / persistence ----

    def validate(self) -> None:
        """Raise :class:`ConfigError` if any field holds an unusable value."""
        if self.units not in UNIT_SYSTEMS:
            raise ConfigError(
                f"Invalid units {self.units!r}.",
                hint=f"Choose one of: {', '.join(UNIT_SYSTEMS)}.",
            )
        if self.color not in COLOR_MODES:
            raise ConfigError(
                f"Invalid color mode {self.color!r}.",
                hint=f"Choose one of: {', '.join(COLOR_MODES)}.",
            )
        if not isinstance(self.forecast_days, int) or not (
            MIN_FORECAST_DAYS <= self.forecast_days <= MAX_FORECAST_DAYS
        ):
            raise ConfigError(
                f"forecast_days must be an integer between {MIN_FORECAST_DAYS} "
                f"and {MAX_FORECAST_DAYS}, got {self.forecast_days!r}.",
            )
        if self.timeout <= 0:
            raise ConfigError(f"timeout must be positive, got {self.timeout!r}.")
        if self.retries < 0:
            raise ConfigError(f"retries cannot be negative, got {self.retries!r}.")
        if self.cache_ttl < 0:
            raise ConfigError(f"cache_ttl cannot be negative, got {self.cache_ttl!r}.")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Build a Config from a dict, ignoring keys we do not know.

        Unknown keys are skipped so an old config file written by a newer
        version still loads.
        """
        known = {f.name: f for f in fields(cls)}
        kwargs: dict = {}
        for key, value in data.items():
            field = known.get(key)
            if field is None:
                continue
            try:
                kwargs[key] = _coerce(value, field.type)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"Invalid value for {key!r}: {value!r} ({exc}).") from exc
        config = cls(**kwargs)
        config.validate()
        return config

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load config from ``path`` (default ``<config dir>/config.json``)."""
        path = path or (default_config_dir() / "config.json")
        if not path.exists():
            return cls()
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Could not read config file {path}: {exc}.") from exc
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Config file {path} is not valid JSON (line {exc.lineno}).",
                hint="Fix the file, or delete it to fall back to defaults.",
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"Config file {path} must contain a JSON object.")
        return cls.from_dict(data)

    def save(self, path: Path | None = None) -> Path:
        """Persist the current settings; returns the path written."""
        self.validate()
        path = path or (default_config_dir() / "config.json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
            path.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not write config file {path}: {exc}.") from exc
        return path


def _coerce(value, type_name: str):
    """Coerce a JSON value to the dataclass field's declared type."""
    if type_name == "bool":
        if isinstance(value, bool):
            return value
        raise TypeError("expected true or false")
    if type_name == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected a number")
        return int(value)
    if type_name == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected a number")
        return float(value)
    if type_name == "str":
        if not isinstance(value, str):
            raise TypeError("expected a string")
        return value
    return value
