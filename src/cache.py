"""A tiny TTL cache on disk.

Weather data changes slowly and geocoding results barely change at all, so
repeated lookups of the same city are served from ``~/.skyquery/cache`` instead
of hitting the network again.

Design notes:
- one JSON file per entry, named after a hash of the cache key;
- the cache is a *best effort* layer: any I/O or decoding problem is treated as
  a miss, never as an error, because a broken cache must not break the CLI.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class Cache:
    """File-backed key/value store where entries expire after ``ttl`` seconds."""

    def __init__(self, directory: Path, ttl: int = 600, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.ttl = max(0, int(ttl))
        self.enabled = enabled and self.ttl > 0

    # ---- public API ----

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key``, or None if missing/expired."""
        if not self.enabled:
            return None
        path = self._path_for(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stored_at = float(payload["stored_at"])
            value = payload["value"]
        except (OSError, ValueError, KeyError, TypeError):
            return None  # missing, unreadable or corrupt -> treat as a miss
        if self._is_expired(stored_at):
            self._discard(path)
            return None
        return value

    def set(self, key: str, value: Any) -> bool:
        """Store ``value`` under ``key``. Returns False if it could not be written."""
        if not self.enabled:
            return False
        path = self._path_for(key)
        payload = {"stored_at": time.time(), "key": key, "value": value}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Write to a temp file first so a crash mid-write cannot leave a
            # half-written entry behind.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def age_of(self, key: str) -> float | None:
        """Seconds since ``key`` was stored, or None if there is no live entry."""
        if not self.enabled:
            return None
        try:
            payload = json.loads(self._path_for(key).read_text(encoding="utf-8"))
            stored_at = float(payload["stored_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self._is_expired(stored_at):
            return None
        return max(0.0, time.time() - stored_at)

    def clear(self) -> int:
        """Delete every cache entry. Returns how many files were removed."""
        removed = 0
        if not self.directory.exists():
            return 0
        for path in self.directory.glob("*.json"):
            if self._discard(path):
                removed += 1
        return removed

    def prune(self) -> int:
        """Delete expired entries only. Returns how many were removed."""
        removed = 0
        if not self.directory.exists():
            return 0
        for path in self.directory.glob("*.json"):
            try:
                stored_at = float(json.loads(path.read_text(encoding="utf-8"))["stored_at"])
            except (OSError, ValueError, KeyError, TypeError):
                stored_at = 0.0  # unreadable entries are garbage; drop them
            if self._is_expired(stored_at) and self._discard(path):
                removed += 1
        return removed

    # ---- internals ----

    def _is_expired(self, stored_at: float) -> bool:
        return (time.time() - stored_at) >= self.ttl

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    @staticmethod
    def _discard(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except OSError:
            return False
