# SkyQuery — Weather CLI

A command-line weather client written in Python. Give it a city name and it
prints current conditions plus a multi-day forecast in your terminal — no API
key, no account, no signup.

SkyQuery is a small project with a specific focus: **doing real HTTP work
safely**. Networks time out, servers return 503, DNS fails, JSON arrives
malformed, and a user will eventually type `Xyzzyville`. Every one of those
paths is handled and tested here — none of them produce a traceback.

```
Yerevan, Armenia
40.19°N, 44.50°E · 988 m · Asia/Yerevan

┌─────────────────────────────────────────────────────────┐
│ 🌤️  32.2°C                                              │
│ Mainly clear                                            │
│ Feels like 34.6°C                                       │
├─────────────────────────────────────────────────────────┤
│ Wind           7.7 km/h SSW (gusts 18)                  │
│ Humidity       34%                                      │
│ Cloud cover    40%                                      │
│ Precipitation  0.0 mm                                   │
│ Pressure       1012 hPa                                 │
│ Sun            06:01 → 20:14                            │
│ UV index       8.2 (very high)                          │
└─────────────────────────────────────────────────────────┘
Observed Mon 03 Aug, 12:30 local time

5-day forecast

DAY                       HI/LO    RAIN  CONDITIONS
Mon 03 Aug   ☁️     36°C / 25°C       —  Overcast
Tue 04 Aug   ⛅     38°C / 24°C       —  Partly cloudy
Wed 05 Aug   ⛅     36°C / 24°C       —  Partly cloudy
Thu 06 Aug   ⛅     33°C / 22°C       —  Partly cloudy
Fri 07 Aug   ⛅     34°C / 23°C       —  Partly cloudy
```

## API used

[**Open-Meteo**](https://open-meteo.com/) — free for non-commercial use and
requires no API key. Two endpoints:

| Purpose | Endpoint |
| --- | --- |
| City name → coordinates | [`geocoding-api.open-meteo.com/v1/search`](https://open-meteo.com/en/docs/geocoding-api) |
| Coordinates → weather | [`api.open-meteo.com/v1/forecast`](https://open-meteo.com/en/docs) |

Conditions come back as numeric [WMO weather codes](https://open-meteo.com/en/docs);
the code → text mapping lives in [src/weather_codes.py](src/weather_codes.py).

## Features

- **City → coordinates → weather**, in one command
- **Current conditions**: temperature, feels-like, wind (speed, gusts, compass
  direction), humidity, cloud cover, pressure, precipitation, sunrise/sunset, UV
- **Forecast** of 1–16 days
- **Multi-city** lookups in a single run, with a compact `--short` table
- **Response caching** on disk, so repeated queries are instant and stay well
  inside the free tier's rate limit
- **Unit configuration** — metric (°C, km/h, mm) or imperial (°F, mph, inch),
  as a flag or a saved default
- **`--json` output** for piping into other tools
- **Graceful error handling** for every failure mode (see below)
- **Terminal-aware rendering**: colour is disabled when piped or when
  `NO_COLOR` is set, and emoji icons fall back to ASCII on legacy code pages

## Setup

Requires Python 3.10+.

```bash
git clone https://github.com/yourname/skyquery.git
cd skyquery

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```bash
python -m src.main Yerevan
```

On Windows you can use the launcher (`py -3 -m src.main Yerevan`), and running
the file directly works too (`python src/main.py Yerevan`).

### Examples

```bash
# current conditions + 5-day forecast
python -m src.main Yerevan

# several cities at once, one line each
python -m src.main Paris Tokyo "New York" --short

# a week ahead, in Fahrenheit
python -m src.main London --days 7 --units imperial

# just right now, no forecast
python -m src.main Berlin --no-forecast

# ambiguous name? see every match before picking
python -m src.main Springfield --list

# machine-readable output
python -m src.main Tokyo --json | jq '.current.temperature'
```

### Options

| Flag | Description |
| --- | --- |
| `-d`, `--days N` | Forecast length, 1–16 days (default: 5) |
| `-u`, `--units {metric,imperial}` | Unit system for this run |
| `-s`, `--short` | One-line-per-city summary table |
| `-l`, `--list` | List every place matching the name; don't fetch weather |
| `--no-forecast` | Current conditions only |
| `--json` | JSON output instead of formatted text |
| `--color {auto,always,never}`, `--no-color` | Control ANSI colour |
| `--timeout SECONDS` | Per-request timeout (default: 10) |
| `--retries N` | Retry attempts for transient failures (default: 2) |
| `--no-cache`, `--cache-ttl SECONDS` | Cache control for this run |
| `--clear-cache` | Delete all cached responses |
| `--set-units`, `--set-days` | Save a default to the config file |
| `--show-config` | Print the resolved configuration |
| `-V`, `--version` | Print the version |

### Configuration

Defaults are stored in `~/.skyquery/config.json` (override the directory with
the `SKYQUERY_HOME` environment variable):

```bash
python -m src.main --set-units imperial
python -m src.main --set-days 7
python -m src.main --show-config
```

```json
{
  "cache_enabled": true,
  "cache_ttl": 600,
  "color": "auto",
  "forecast_days": 7,
  "language": "en",
  "retries": 2,
  "timeout": 10.0,
  "units": "imperial"
}
```

Precedence is **CLI flags → config file → built-in defaults**.

### Caching

Responses are cached as JSON under `~/.skyquery/cache`, keyed by coordinates,
forecast length and unit system, and expire after 10 minutes by default. A
cached report is labelled in the output:

```
(served from cache, 45s old)
```

The cache is strictly best-effort: an unreadable, corrupt or unwritable cache
entry is treated as a miss, never as an error.

## Error handling notes

This was the main thing the project set out to practise, so it is worth
spelling out. Every expected failure becomes a `SkyQueryError` subclass
([src/errors.py](src/errors.py)), which the CLI prints as a one-line message
with an actionable hint — never a stack trace. The process exits `0` on
success, `1` on error, `2` on a usage problem.

| Situation | Handling |
| --- | --- |
| No internet / DNS failure | `NetworkError`, retried, then "Could not reach the weather service." |
| Request times out | `ApiTimeoutError` after the configured timeout, retried with backoff |
| TLS/certificate failure | Reported immediately — **not** retried, since it will not fix itself |
| HTTP 5xx, 429, 408 | Retried with exponential backoff (0.5s → 1s → 2s), then reported |
| HTTP 4xx | Reported immediately with the API's own `reason` text |
| Rate limited (429) | Explicit message pointing at the free tier's limits |
| Response is not JSON | `ResponseParseError` — usually a captive portal or proxy |
| JSON missing fields | Missing values render as `—`; only a missing `current` block is fatal |
| Unknown weather code | Falls back to "Unknown conditions" rather than failing |
| City not found | `CityNotFoundError` with a spelling hint |
| One city fails out of several | The others still print; exit code reflects the failure |
| Corrupt cache or config file | Cache: silently ignored. Config: reported with a "delete it" hint |
| `Ctrl+C` | Clean "Cancelled." message, exit code 130 |

Retries only ever apply to failures that are plausibly transient. A `400` or a
bad certificate is not retried, because repeating the request cannot help.

## Project structure

```
skyquery/
├── src/
│   ├── main.py           # CLI: argument parsing, config resolution, exit codes
│   ├── api_client.py     # HTTP: geocoding, forecast, retries, parsing
│   ├── display.py        # Rendering: panels, tables, colour, JSON
│   ├── cache.py          # TTL cache on disk
│   ├── config.py         # Defaults, validation, persistence
│   ├── weather_codes.py  # WMO code → description + icon
│   └── errors.py         # Exception hierarchy
├── tests/                # 113 tests, no network access
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

The layering is deliberate: `api_client` knows nothing about printing,
`display` knows nothing about HTTP, and `main` is the only module that touches
both.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

```
113 passed in 1.86s
```

The suite never touches the network — a stub session replays canned Open-Meteo
payloads, including the failure cases (timeouts, 500s, rate limits, truncated
JSON, non-JSON bodies). Config and cache are redirected to a temp directory via
`SKYQUERY_HOME`, so running the tests cannot touch your real `~/.skyquery`.

## What I learned

- Consuming a REST API end to end: query parameters, status codes, JSON shapes
- Why network code needs timeouts on *every* request, and why retries must
  distinguish transient failures from permanent ones
- Defensive parsing — treating every field in a response as possibly missing
- Keeping HTTP, formatting and CLI concerns in separate modules, which is what
  made the whole thing testable without a network connection
- Terminal details that only show up in practice: ANSI escapes have zero
  printed width, emoji occupy two cells, and Windows code pages cannot always
  encode them

## Roadmap

- [ ] Query history with a temperature trend over time
- [ ] Hourly forecast view
- [ ] Severe-weather alerts

## License

MIT
