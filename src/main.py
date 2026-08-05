"""SkyQuery command-line entry point.

    py -m src.main Yerevan
    py -m src.main Paris Tokyo --short --units imperial
    py src/main.py "New York" --days 7 --json

This module owns argument parsing, config resolution and error reporting; the
actual work lives in :mod:`src.api_client` and :mod:`src.display`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow both `py -m src.main` and `py src/main.py` by making the project root
# importable and declaring the package when run as a plain script (PEP 366).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "src"

from . import __version__
from .api_client import WeatherClient, WeatherReport, unique_locations
from .cache import Cache
from .config import (
    COLOR_MODES,
    MAX_FORECAST_DAYS,
    MIN_FORECAST_DAYS,
    UNIT_SYSTEMS,
    Config,
    default_config_dir,
)
from .display import (
    Theme,
    render_error,
    render_location_choices,
    render_reports,
    render_summary_table,
    report_to_dict,
    should_use_color,
    supports_unicode,
    terminal_width,
)
from .errors import SkyQueryError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skyquery",
        description="Current weather and forecast for any city, via the free Open-Meteo API.",
        epilog=(
            "examples:\n"
            "  skyquery Yerevan\n"
            "  skyquery Paris Tokyo \"New York\" --short\n"
            "  skyquery London --days 7 --units imperial\n"
            "  skyquery Berlin --list          # show every matching place\n"
            "  skyquery --set-units imperial   # remember a preference\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("cities", nargs="*", metavar="CITY",
                        help="one or more city names (quote names containing spaces)")

    query = parser.add_argument_group("query options")
    query.add_argument("-d", "--days", type=int, metavar="N",
                       help=f"forecast length, {MIN_FORECAST_DAYS}-{MAX_FORECAST_DAYS} days")
    query.add_argument("-u", "--units", choices=UNIT_SYSTEMS,
                       help="metric (°C, km/h, mm) or imperial (°F, mph, inch)")
    query.add_argument("--timeout", type=float, metavar="SECONDS",
                       help="per-request timeout (default: 10)")
    query.add_argument("--retries", type=int, metavar="N",
                       help="retry attempts for transient network failures (default: 2)")
    query.add_argument("-l", "--list", action="store_true", dest="list_matches",
                       help="list every place matching the name instead of fetching weather")

    output = parser.add_argument_group("output options")
    output.add_argument("-s", "--short", action="store_true",
                        help="one-line-per-city summary table")
    output.add_argument("--no-forecast", action="store_true",
                        help="show current conditions only")
    output.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable JSON instead of formatted text")
    output.add_argument("--color", choices=COLOR_MODES, default=None,
                        help="colour output: auto (default), always, never")
    output.add_argument("--no-color", action="store_true",
                        help="shorthand for --color never")

    cache_group = parser.add_argument_group("cache and config")
    cache_group.add_argument("--no-cache", action="store_true",
                             help="ignore cached responses for this run")
    cache_group.add_argument("--cache-ttl", type=int, metavar="SECONDS",
                             help="how long cached responses stay fresh (default: 600)")
    cache_group.add_argument("--clear-cache", action="store_true",
                             help="delete all cached responses and exit")
    cache_group.add_argument("--set-units", choices=UNIT_SYSTEMS, metavar="SYSTEM",
                             help="save a default unit system to the config file and exit")
    cache_group.add_argument("--set-days", type=int, metavar="N",
                             help="save a default forecast length to the config file and exit")
    cache_group.add_argument("--show-config", action="store_true",
                             help="print the resolved configuration and exit")

    parser.add_argument("-V", "--version", action="version",
                        version=f"SkyQuery {__version__}")
    return parser


def resolve_config(args: argparse.Namespace) -> Config:
    """Merge the stored config with CLI overrides."""
    config = Config.load()
    if args.units:
        config.units = args.units
    if args.days is not None:
        config.forecast_days = args.days
    if args.timeout is not None:
        config.timeout = args.timeout
    if args.retries is not None:
        config.retries = args.retries
    if args.cache_ttl is not None:
        config.cache_ttl = args.cache_ttl
    if args.no_cache:
        config.cache_enabled = False
    if args.no_color:
        config.color = "never"
    elif args.color:
        config.color = args.color
    config.validate()
    return config


def make_theme(config: Config) -> Theme:
    return Theme(
        enabled=should_use_color(config.color),
        unicode_icons=supports_unicode(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _enable_windows_ansi()

    # Never let an unencodable glyph turn into a traceback on a legacy console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        config = resolve_config(args)
    except SkyQueryError as error:
        print(render_error(error, Theme(enabled=should_use_color("auto", sys.stderr))),
              file=sys.stderr)
        return EXIT_ERROR

    theme = make_theme(config)

    try:
        # --- commands that do not need the network ---
        if args.clear_cache:
            return _clear_cache(config, theme)
        if args.set_units or args.set_days is not None:
            return _save_settings(config, args, theme)
        if args.show_config:
            return _show_config(config, args.as_json)

        if not args.cities:
            parser.print_help()
            return EXIT_USAGE

        return _run_query(args, config, theme)

    except SkyQueryError as error:
        print(render_error(error, theme), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _run_query(args: argparse.Namespace, config: Config, theme: Theme) -> int:
    cache = Cache(
        directory=default_config_dir() / "cache",
        ttl=config.cache_ttl,
        enabled=config.cache_enabled,
    )

    reports: list[WeatherReport] = []
    failures: list[tuple[str, SkyQueryError]] = []

    with WeatherClient(config=config, cache=cache) as client:
        if args.list_matches:
            return _list_matches(client, args.cities, theme)

        for city in args.cities:
            try:
                reports.append(client.get_report(city))
            except SkyQueryError as error:
                # One bad city should not discard the cities that did resolve.
                failures.append((city, error))

    if reports:
        _print_reports(reports, args, config, theme)

    for city, error in failures:
        prefix = theme.dim(f"[{city}] ") if len(args.cities) > 1 else ""
        print(f"{prefix}{render_error(error, theme)}", file=sys.stderr)

    if not reports:
        return EXIT_ERROR
    return EXIT_ERROR if failures else EXIT_OK


def _print_reports(
    reports: list[WeatherReport],
    args: argparse.Namespace,
    config: Config,
    theme: Theme,
) -> None:
    if args.as_json:
        payload = [report_to_dict(r) for r in reports]
        print(json.dumps(payload[0] if len(payload) == 1 else payload,
                         indent=2, ensure_ascii=False))
        return

    width = terminal_width()
    if args.short:
        print(render_summary_table(reports, theme=theme, width=width))
        return

    print(render_reports(
        reports,
        theme=theme,
        width=width,
        show_forecast=not args.no_forecast,
        detailed=True,
    ))


def _list_matches(client: WeatherClient, cities: list[str], theme: Theme) -> int:
    """Show every geocoding candidate — useful for ambiguous names."""
    exit_code = EXIT_OK
    for index, city in enumerate(cities):
        if index:
            print()
        try:
            locations = unique_locations(client.geocode(city, limit=10))
        except SkyQueryError as error:
            print(render_error(error, theme), file=sys.stderr)
            exit_code = EXIT_ERROR
            continue
        print(render_location_choices(locations, theme, title=f"Matches for {city!r}:"))
    return exit_code


def _clear_cache(config: Config, theme: Theme) -> int:
    cache = Cache(directory=default_config_dir() / "cache", ttl=config.cache_ttl, enabled=True)
    removed = cache.clear()
    noun = "entry" if removed == 1 else "entries"
    print(f"Cleared {removed} cached {noun} from {cache.directory}.")
    return EXIT_OK


def _save_settings(config: Config, args: argparse.Namespace, theme: Theme) -> int:
    if args.set_units:
        config.units = args.set_units
    if args.set_days is not None:
        config.forecast_days = args.set_days
    config.validate()
    path = config.save()
    print(f"Saved settings to {path}:")
    print(f"  units         {config.units} ({config.temperature_symbol})")
    print(f"  forecast_days {config.forecast_days}")
    return EXIT_OK


def _show_config(config: Config, as_json: bool) -> int:
    path = default_config_dir() / "config.json"
    if as_json:
        print(json.dumps({"path": str(path), "exists": path.exists(), **config.to_dict()},
                         indent=2))
        return EXIT_OK
    print(f"Config file: {path}{'' if path.exists() else '  (not created yet)'}")
    for key, value in sorted(config.to_dict().items()):
        print(f"  {key:<14} {value}")
    print(f"Cache directory: {default_config_dir() / 'cache'}")
    return EXIT_OK


def _enable_windows_ansi() -> None:
    """Ask the Windows console to interpret ANSI escapes.

    Windows Terminal already does; the legacy conhost needs the
    ENABLE_VIRTUAL_TERMINAL_PROCESSING flag set explicitly. Failure is fine —
    colour detection simply falls back to plain text.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover - purely cosmetic
        pass


if __name__ == "__main__":
    sys.exit(main())
