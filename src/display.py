"""Terminal rendering for SkyQuery.

This module is deliberately free of any network or config-file access: it takes
:class:`~src.api_client.WeatherReport` objects and returns strings. That makes
the formatting easy to test without touching the API.

Two terminal realities are handled here:
- colour is opt-out (``--no-color``, ``NO_COLOR``, or a redirected stdout);
- Windows' legacy code pages cannot encode weather emoji, so icons fall back to
  ASCII when the output stream says it cannot represent them.
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from datetime import datetime
from typing import Iterable, Sequence, TextIO

from .api_client import DailyForecast, Location, WeatherReport

BOX = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "h": "─", "v": "│", "ml": "├", "mr": "┤",
}

MIN_WIDTH = 46
MAX_WIDTH = 78


class Theme:
    """ANSI styling that turns into a no-op when colour is disabled."""

    RESET = "\033[0m"

    def __init__(self, enabled: bool = True, unicode_icons: bool = True) -> None:
        self.enabled = enabled
        self.unicode_icons = unicode_icons

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}{self.RESET}" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("\033[1m", text)

    def dim(self, text: str) -> str:
        return self._wrap("\033[2m", text)

    def cyan(self, text: str) -> str:
        return self._wrap("\033[36m", text)

    def yellow(self, text: str) -> str:
        return self._wrap("\033[33m", text)

    def red(self, text: str) -> str:
        return self._wrap("\033[31m", text)

    def green(self, text: str) -> str:
        return self._wrap("\033[32m", text)

    def blue(self, text: str) -> str:
        return self._wrap("\033[34m", text)

    def magenta(self, text: str) -> str:
        return self._wrap("\033[35m", text)

    def temperature(self, celsius_like: float | None, text: str) -> str:
        """Colour a temperature by how cold/hot it reads (metric thresholds)."""
        if celsius_like is None or not self.enabled:
            return text
        if celsius_like <= 0:
            return self.cyan(text)
        if celsius_like <= 12:
            return self.blue(text)
        if celsius_like <= 24:
            return self.green(text)
        if celsius_like <= 32:
            return self.yellow(text)
        return self.red(text)

    def icon(self, condition) -> str:
        return condition.icon if self.unicode_icons else condition.ascii_icon


# --------------------------------------------------------------------------
# Capability detection
# --------------------------------------------------------------------------


def should_use_color(mode: str = "auto", stream: TextIO | None = None) -> bool:
    """Decide whether ANSI colour is appropriate.

    Honours the ``NO_COLOR`` convention and avoids emitting escapes into pipes.
    """
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def supports_unicode(stream: TextIO | None = None) -> bool:
    """True when ``stream`` can actually encode the emoji icons."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "☀️❄️⛈️".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def terminal_width(default: int = 60) -> int:
    """Usable width, clamped to a range that keeps the panels readable."""
    try:
        width = shutil.get_terminal_size((default, 24)).columns
    except (OSError, ValueError):
        width = default
    return max(MIN_WIDTH, min(width - 1, MAX_WIDTH))


# --------------------------------------------------------------------------
# Width-aware text helpers
# --------------------------------------------------------------------------


def display_width(text: str) -> int:
    """Approximate the printed width of ``text`` in terminal cells.

    Emoji and CJK glyphs occupy two cells; combining marks and variation
    selectors occupy none. Getting this right is what keeps the box borders
    aligned once icons are involved.
    """
    width = 0
    for char in text:
        if unicodedata.combining(char) or char in "️︎‍":
            continue
        if unicodedata.east_asian_width(char) in ("W", "F") or ord(char) >= 0x1F300:
            width += 2
        else:
            width += 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad ``text`` to ``width`` printed cells (truncating when too long)."""
    current = display_width(text)
    if current > width:
        text = truncate(text, width)
        current = display_width(text)
    filler = " " * max(0, width - current)
    if align == "right":
        return filler + text
    if align == "center":
        left = len(filler) // 2
        return " " * left + text + " " * (len(filler) - left)
    return text + filler


def pad_styled(styled: str, plain: str, width: int, align: str = "left") -> str:
    """Pad a coloured string to ``width`` using its *unstyled* width.

    ANSI escapes print as zero cells, so padding ``styled`` directly would both
    over-count the width and risk truncating an escape sequence mid-way.
    """
    current = display_width(plain)
    if current > width:
        return pad(plain, width, align)  # too long: drop styling rather than cut escapes
    filler = " " * (width - current)
    if align == "right":
        return filler + styled
    if align == "center":
        left = len(filler) // 2
        return " " * left + styled + " " * (len(filler) - left)
    return styled + filler


def truncate(text: str, width: int) -> str:
    """Shorten ``text`` to at most ``width`` cells, using an ellipsis."""
    if display_width(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    out = ""
    for char in text:
        if display_width(out + char) > width - 1:
            break
        out += char
    return out + "…"


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


def render_report(
    report: WeatherReport,
    theme: Theme | None = None,
    width: int | None = None,
    show_forecast: bool = True,
    detailed: bool = True,
) -> str:
    """Render one city's full report: header, current conditions, forecast."""
    theme = theme or Theme()
    width = width or terminal_width()
    lines: list[str] = []
    lines.extend(_header_lines(report, theme, width))
    lines.extend(_current_lines(report, theme, width, detailed=detailed))
    if show_forecast and report.daily:
        lines.append("")
        lines.extend(_forecast_lines(report, theme, width))
    footer = _footer(report, theme)
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines)


def render_reports(
    reports: Sequence[WeatherReport],
    theme: Theme | None = None,
    width: int | None = None,
    show_forecast: bool = True,
    detailed: bool = True,
) -> str:
    """Render several cities, separated by a blank line and a rule."""
    theme = theme or Theme()
    width = width or terminal_width()
    blocks = [
        render_report(r, theme=theme, width=width, show_forecast=show_forecast, detailed=detailed)
        for r in reports
    ]
    separator = "\n\n" + theme.dim("·" * width) + "\n\n"
    return separator.join(blocks)


def render_summary_table(
    reports: Sequence[WeatherReport],
    theme: Theme | None = None,
    width: int | None = None,
) -> str:
    """One line per city — the compact view for multi-city lookups."""
    theme = theme or Theme()
    width = width or terminal_width()
    if not reports:
        return ""

    name_width = max(12, min(24, max(display_width(r.location.name) for r in reports)))
    rows: list[str] = []
    header = "  ".join([
        pad("CITY", name_width),
        pad("NOW", 9, "right"),
        pad("FEELS", 9, "right"),
        pad("HI/LO", 13, "right"),
        "CONDITIONS",
    ])
    rows.append(theme.bold(header))
    rows.append(theme.dim(BOX["h"] * min(width, display_width(header) + 2)))

    for report in reports:
        current = report.current
        temp_unit = report.unit("temperature_2m", "°")
        day = report.daily[0] if report.daily else None
        hi_lo = "—"
        if day and (day.temp_max is not None or day.temp_min is not None):
            hi_lo = f"{_num(day.temp_max, 0)}/{_num(day.temp_min, 0)}{temp_unit}"
        condition = current.condition
        rows.append("  ".join([
            pad(report.location.name, name_width),
            theme.temperature(
                _metric_equiv(current.temperature, temp_unit),
                pad(f"{_num(current.temperature, 1)}{temp_unit}", 9, "right"),
            ),
            theme.dim(pad(f"{_num(current.feels_like, 1)}{temp_unit}", 9, "right")),
            pad(hi_lo, 13, "right"),
            f"{theme.icon(condition)} {condition.description}",
        ]))
    return "\n".join(rows)


def _header_lines(report: WeatherReport, theme: Theme, width: int) -> list[str]:
    location = report.location
    title = theme.bold(truncate(location.label, width - 2))
    meta_bits = [location.coordinates]
    if location.elevation is not None:
        meta_bits.append(f"{location.elevation:.0f} m")
    if report.timezone:
        meta_bits.append(report.timezone)
    return [title, theme.dim(truncate(" · ".join(meta_bits), width - 2)), ""]


def _current_lines(report: WeatherReport, theme: Theme, width: int, detailed: bool) -> list[str]:
    current = report.current
    condition = current.condition
    temp_unit = report.unit("temperature_2m", "°")
    wind_unit = pretty_unit(report.unit("wind_speed_10m", "km/h"))
    precip_unit = pretty_unit(report.unit("precipitation", "mm"))

    inner = width - 2
    temp_text = f"{_num(current.temperature, 1)}{temp_unit}"
    big = f"{theme.icon(condition)}  {theme.bold(theme.temperature(_metric_equiv(current.temperature, temp_unit), temp_text))}"
    plain_big = f"{theme.icon(condition)}  {temp_text}"

    lines = [
        BOX["tl"] + BOX["h"] * inner + BOX["tr"],
        _boxed(big, plain_big, inner),
        _boxed(theme.dim(condition.description), condition.description, inner),
    ]

    feels = f"Feels like {_num(current.feels_like, 1)}{temp_unit}"
    lines.append(_boxed(feels, feels, inner))

    if detailed:
        lines.append(BOX["ml"] + BOX["h"] * inner + BOX["mr"])
        wind = f"{_num(current.wind_speed, 1)} {wind_unit} {current.wind_compass}"
        if current.wind_gusts is not None:
            wind += f" (gusts {_num(current.wind_gusts, 0)})"
        stats = [
            ("Wind", wind),
            ("Humidity", f"{_num(current.humidity, 0)}%"),
            ("Cloud cover", f"{_num(current.cloud_cover, 0)}%"),
            ("Precipitation", f"{_num(current.precipitation, 1)} {precip_unit}"),
            ("Pressure", f"{_num(current.pressure, 0)} hPa"),
        ]
        if report.daily:
            today = report.daily[0]
            if today.sunrise and today.sunset:
                stats.append(("Sun", f"{today.sunrise:%H:%M} → {today.sunset:%H:%M}"))
            if today.uv_index is not None:
                stats.append(("UV index", f"{_num(today.uv_index, 1)} ({_uv_label(today.uv_index)})"))

        label_width = max(len(label) for label, _ in stats)
        for label, value in stats:
            plain = f"{label.ljust(label_width)}  {value}"
            styled = f"{theme.dim(label.ljust(label_width))}  {value}"
            lines.append(_boxed(styled, plain, inner))

    lines.append(BOX["bl"] + BOX["h"] * inner + BOX["br"])
    if current.time:
        lines.append(theme.dim(f"Observed {current.time:%a %d %b, %H:%M} local time"))
    return lines


def _forecast_lines(report: WeatherReport, theme: Theme, width: int) -> list[str]:
    temp_unit = report.unit("temperature_2m", "°")
    days = report.daily
    lines = [theme.bold(f"{len(days)}-day forecast"), ""]

    icon_width = 3
    day_width = 11  # fits "Mon 03 Aug"
    temp_width = 13
    rain_width = 6

    header = "  ".join([
        pad("DAY", day_width),
        pad("", icon_width),
        pad("HI/LO", temp_width, "right"),
        pad("RAIN", rain_width, "right"),
        "CONDITIONS",
    ])
    lines.append(theme.dim(header))

    description_width = max(12, width - day_width - icon_width - temp_width - rain_width - 8)

    for day in days:
        condition = day.condition
        hi = f"{_num(day.temp_max, 0)}{temp_unit}"
        lo = f"{_num(day.temp_min, 0)}{temp_unit}"
        hi_lo = pad_styled(
            theme.temperature(_metric_equiv(day.temp_max, temp_unit), hi)
            + theme.dim(" / ")
            + theme.dim(lo),
            f"{hi} / {lo}",
            temp_width,
            "right",
        )

        lines.append("  ".join([
            pad(f"{day.day_name} {day.date_label}", day_width),
            pad(theme.icon(condition), icon_width),
            hi_lo,
            pad(_rain_text(day), rain_width, "right"),
            truncate(condition.description, description_width),
        ]))
    return lines


def _rain_text(day: DailyForecast) -> str:
    if day.precipitation_chance is not None and day.precipitation_chance > 0:
        return f"{day.precipitation_chance:.0f}%"
    if day.precipitation:
        return f"{day.precipitation:.1f}"
    return "—"


def _footer(report: WeatherReport, theme: Theme) -> str:
    if not report.from_cache:
        return ""
    if report.cache_age is None:
        return theme.dim("(served from cache)")
    return theme.dim(f"(served from cache, {_age_text(report.cache_age)} old)")


def _boxed(styled: str, plain: str, inner: int) -> str:
    """Draw one box row, padding by the *plain* text's width.

    ANSI escapes have zero printed width, so padding must be computed from the
    unstyled string or every border after the first colour would drift.
    """
    padding = " " * max(0, inner - 2 - display_width(plain))
    return f"{BOX['v']} {styled}{padding} {BOX['v']}"


# --------------------------------------------------------------------------
# Small formatting helpers
# --------------------------------------------------------------------------


# Open-Meteo spells a few units in ways that read oddly in a sentence.
_UNIT_LABELS = {"mp/h": "mph", "km/h": "km/h", "inch": "in", "kn": "kt"}


def pretty_unit(unit: str) -> str:
    """Tidy the API's raw unit strings for display."""
    return _UNIT_LABELS.get(unit.strip(), unit)


def _num(value: float | None, digits: int = 1) -> str:
    """Format a possibly-missing number without a trailing ``.0`` when digits=0."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _metric_equiv(value: float | None, unit: str) -> float | None:
    """Normalise a temperature to Celsius so colour thresholds stay meaningful."""
    if value is None:
        return None
    if "F" in unit.upper():
        return (value - 32) * 5 / 9
    return value


def _uv_label(uv: float) -> str:
    if uv < 3:
        return "low"
    if uv < 6:
        return "moderate"
    if uv < 8:
        return "high"
    if uv < 11:
        return "very high"
    return "extreme"


def _age_text(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def render_location_choices(
    locations: Iterable[Location],
    theme: Theme | None = None,
    title: str | None = "Multiple matches:",
) -> str:
    """List ambiguous geocoding matches so the user can disambiguate."""
    theme = theme or Theme()
    lines = [theme.bold(title)] if title else []
    for index, location in enumerate(locations, start=1):
        detail = location.coordinates
        if location.population:
            detail += f" · pop. {location.population:,}"
        lines.append(f"  {index}. {location.label}  {theme.dim(detail)}")
    return "\n".join(lines)


def render_error(error: Exception, theme: Theme | None = None) -> str:
    """Format an error the way the CLI prints it to stderr."""
    theme = theme or Theme()
    message = getattr(error, "message", None) or str(error)
    lines = [f"{theme.red('Error:')} {message}"]
    hint = getattr(error, "hint", None)
    if hint:
        lines.append(theme.dim(f"Hint: {hint}"))
    return "\n".join(lines)


def report_to_dict(report: WeatherReport) -> dict:
    """Serialise a report for ``--json`` output."""

    def stamp(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    current = report.current
    return {
        "location": {
            "name": report.location.name,
            "label": report.location.label,
            "country": report.location.country,
            "country_code": report.location.country_code,
            "admin1": report.location.admin1,
            "latitude": report.location.latitude,
            "longitude": report.location.longitude,
            "elevation": report.location.elevation,
            "timezone": report.timezone,
        },
        "current": {
            "time": stamp(current.time),
            "temperature": current.temperature,
            "feels_like": current.feels_like,
            "humidity": current.humidity,
            "precipitation": current.precipitation,
            "cloud_cover": current.cloud_cover,
            "pressure": current.pressure,
            "wind_speed": current.wind_speed,
            "wind_direction": current.wind_direction,
            "wind_compass": current.wind_compass,
            "wind_gusts": current.wind_gusts,
            "is_day": current.is_day,
            "weather_code": current.weather_code,
            "condition": current.condition.description,
        },
        "daily": [
            {
                "date": stamp(day.date),
                "weather_code": day.weather_code,
                "condition": day.condition.description,
                "temp_max": day.temp_max,
                "temp_min": day.temp_min,
                "feels_like_max": day.feels_like_max,
                "precipitation": day.precipitation,
                "precipitation_chance": day.precipitation_chance,
                "wind_max": day.wind_max,
                "uv_index": day.uv_index,
                "sunrise": stamp(day.sunrise),
                "sunset": stamp(day.sunset),
            }
            for day in report.daily
        ],
        "units": report.units,
        "from_cache": report.from_cache,
    }
