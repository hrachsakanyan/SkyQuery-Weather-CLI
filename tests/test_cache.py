"""Tests for the on-disk TTL cache."""

from __future__ import annotations

import json
import time

from src.cache import Cache


def test_set_then_get_round_trips_a_value(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    assert cache.set("key", {"temp": 21.5}) is True
    assert cache.get("key") == {"temp": 21.5}


def test_missing_key_is_a_miss(tmp_path):
    assert Cache(tmp_path, ttl=60).get("nothing-here") is None


def test_entries_expire_after_the_ttl(tmp_path):
    cache = Cache(tmp_path, ttl=1)
    cache.set("key", "value")
    assert cache.get("key") == "value"

    # Rewrite the timestamp instead of sleeping, to keep the suite fast.
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stored_at"] = time.time() - 5
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get("key") is None
    assert not path.exists()  # expired entries are cleaned up on read


def test_disabled_cache_stores_nothing(tmp_path):
    cache = Cache(tmp_path, ttl=60, enabled=False)
    assert cache.set("key", "value") is False
    assert cache.get("key") is None


def test_zero_ttl_disables_the_cache(tmp_path):
    cache = Cache(tmp_path, ttl=0)
    assert cache.enabled is False
    assert cache.set("key", "value") is False


def test_corrupt_entry_is_treated_as_a_miss(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("key", "value")
    next(tmp_path.glob("*.json")).write_text("{not json", encoding="utf-8")
    assert cache.get("key") is None


def test_unserialisable_value_fails_softly(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    assert cache.set("key", {1, 2, 3}) is False  # sets are not JSON
    assert cache.get("key") is None


def test_different_keys_do_not_collide(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    assert (cache.get("a"), cache.get("b")) == (1, 2)


def test_age_of_reports_a_live_entry(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("key", "value")
    age = cache.age_of("key")
    assert age is not None and 0 <= age < 5
    assert cache.age_of("absent") is None


def test_clear_removes_every_entry(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.clear() == 2
    assert cache.get("a") is None


def test_clear_on_a_missing_directory_is_a_no_op(tmp_path):
    assert Cache(tmp_path / "never-created", ttl=60).clear() == 0


def test_prune_removes_only_expired_entries(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("fresh", 1)
    cache.set("stale", 2)

    for path in tmp_path.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["key"] == "stale":
            payload["stored_at"] = time.time() - 999
            path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.prune() == 1
    assert cache.get("fresh") == 1


def test_no_temp_files_are_left_behind(tmp_path):
    cache = Cache(tmp_path, ttl=60)
    cache.set("key", "value")
    assert list(tmp_path.glob("*.tmp")) == []
