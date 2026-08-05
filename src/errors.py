"""Exception hierarchy for SkyQuery.

Every failure the CLI can recover from is expressed as a SkyQueryError so that
``main`` only needs one ``except`` clause to print a friendly message instead of
a traceback.
"""


class SkyQueryError(Exception):
    """Base class for all expected SkyQuery failures.

    ``hint`` carries an optional actionable suggestion shown under the error.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class NetworkError(SkyQueryError):
    """The request never reached the API (DNS failure, no route, refused)."""


class TimeoutError_(SkyQueryError):
    """The API did not answer within the configured timeout."""


# Exported under a friendlier name; the trailing underscore above only avoids
# shadowing the builtin ``TimeoutError`` inside this module.
ApiTimeoutError = TimeoutError_


class ApiError(SkyQueryError):
    """The API answered, but with an error status code."""

    def __init__(self, message: str, status_code: int | None = None,
                 hint: str | None = None) -> None:
        super().__init__(message, hint)
        self.status_code = status_code


class ResponseParseError(SkyQueryError):
    """The response was not JSON, or was missing fields we require."""


class CityNotFoundError(SkyQueryError):
    """Geocoding returned no match for the requested city name."""

    def __init__(self, city: str, hint: str | None = None) -> None:
        super().__init__(f"No location found for {city!r}.", hint)
        self.city = city


class ConfigError(SkyQueryError):
    """The config file is unreadable, or holds an invalid value."""
