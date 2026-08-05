"""WMO weather interpretation codes used by Open-Meteo.

Open-Meteo reports conditions as a numeric WMO code rather than free text, so
the CLI owns the code -> human description mapping.

Reference: https://open-meteo.com/en/docs (section "WMO Weather interpretation
codes").
"""

from __future__ import annotations

from typing import NamedTuple


class Condition(NamedTuple):
    """A decoded weather code."""

    code: int
    description: str
    icon: str       # emoji, used when the terminal can encode it
    ascii_icon: str  # plain fallback for legacy code pages


_UNKNOWN = Condition(-1, "Unknown conditions", "\N{BLACK QUESTION MARK ORNAMENT}", "(?)")

# code -> (description, day icon, ascii icon)
_CODES: dict[int, tuple[str, str, str]] = {
    0: ("Clear sky", "☀️", "(o)"),
    1: ("Mainly clear", "\N{WHITE SUN WITH SMALL CLOUD}️", "(o-)"),
    2: ("Partly cloudy", "⛅", "(o~)"),
    3: ("Overcast", "☁️", "(~~)"),
    45: ("Fog", "\N{FOG}", "(==)"),
    48: ("Depositing rime fog", "\N{FOG}", "(==)"),
    51: ("Light drizzle", "\N{WHITE SUN BEHIND CLOUD WITH RAIN}️", "(,,)"),
    53: ("Moderate drizzle", "\N{WHITE SUN BEHIND CLOUD WITH RAIN}️", "(,,)"),
    55: ("Dense drizzle", "\N{CLOUD WITH RAIN}️", "(,,,)"),
    56: ("Light freezing drizzle", "\N{CLOUD WITH RAIN}️", "(*,)"),
    57: ("Dense freezing drizzle", "\N{CLOUD WITH RAIN}️", "(*,,)"),
    61: ("Slight rain", "\N{CLOUD WITH RAIN}️", "(//)"),
    63: ("Moderate rain", "\N{CLOUD WITH RAIN}️", "(///)"),
    65: ("Heavy rain", "\N{CLOUD WITH RAIN}️", "(////)"),
    66: ("Light freezing rain", "\N{CLOUD WITH SNOW}️", "(*/)"),
    67: ("Heavy freezing rain", "\N{CLOUD WITH SNOW}️", "(*//)"),
    71: ("Slight snow fall", "\N{CLOUD WITH SNOW}️", "(**)"),
    73: ("Moderate snow fall", "\N{CLOUD WITH SNOW}️", "(***)"),
    75: ("Heavy snow fall", "❄️", "(****)"),
    77: ("Snow grains", "❄️", "(**)"),
    80: ("Slight rain showers", "\N{WHITE SUN BEHIND CLOUD WITH RAIN}️", "(/o)"),
    81: ("Moderate rain showers", "\N{CLOUD WITH RAIN}️", "(//o)"),
    82: ("Violent rain showers", "\N{CLOUD WITH RAIN}️", "(///!)"),
    85: ("Slight snow showers", "\N{CLOUD WITH SNOW}️", "(*o)"),
    86: ("Heavy snow showers", "\N{CLOUD WITH SNOW}️", "(**o)"),
    95: ("Thunderstorm", "⛈️", "(/!\\)"),
    96: ("Thunderstorm with slight hail", "⛈️", "(/!o)"),
    99: ("Thunderstorm with heavy hail", "⛈️", "(/!O)"),
}

# Codes whose icon differs at night (clear/cloudy states only).
_NIGHT_ICONS: dict[int, tuple[str, str]] = {
    0: ("\N{CRESCENT MOON}", "(C)"),
    1: ("\N{CRESCENT MOON}", "(C-)"),
    2: ("\N{CLOUD}️", "(C~)"),
}


def describe(code: int | None, is_day: bool = True) -> Condition:
    """Return the :class:`Condition` for a WMO ``code``.

    Unknown or missing codes degrade to a neutral "Unknown conditions" entry
    rather than raising — a surprise code should not kill the whole report.
    """
    if code is None:
        return _UNKNOWN
    try:
        code = int(code)
    except (TypeError, ValueError):
        return _UNKNOWN

    entry = _CODES.get(code)
    if entry is None:
        return _UNKNOWN._replace(code=code)

    description, icon, ascii_icon = entry
    if not is_day and code in _NIGHT_ICONS:
        icon, ascii_icon = _NIGHT_ICONS[code]
    return Condition(code, description, icon, ascii_icon)


def is_severe(code: int | None) -> bool:
    """True for conditions worth highlighting (storms, heavy rain/snow)."""
    return code in {65, 67, 75, 82, 86, 95, 96, 99}
