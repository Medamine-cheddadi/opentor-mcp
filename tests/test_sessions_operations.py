"""Session listing, missing-file, malformed-file, and deletion behavior."""

import asyncio
import json
import stat
import time

import pytest

from tor_mcp.sessions import SessionStore


def run(coro):
    return asyncio.run(coro)


def test_constructor_hardens_existing_regular_session_files(tmp_path):
    storage = tmp_path / "sessions"
    storage.mkdir()
    existing = storage / "existing.json"
    existing.write_text("{}")
    existing.chmod(0o644)

    SessionStore(storage)

    assert stat.S_IMODE(existing.stat().st_mode) == 0o600


def test_load_returns_none_for_missing_session(tmp_path):
    store = SessionStore(tmp_path / "sessions")

    assert run(store.load("missing")) is None


def test_load_surfaces_malformed_json_without_modifying_it(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    malformed = store.storage_dir / "broken.json"
    malformed.write_text("not-json")

    with pytest.raises(json.JSONDecodeError):
        run(store.load("broken"))

    assert malformed.read_text() == "not-json"


def test_load_rejects_non_object_json_and_listing_skips_it(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    invalid = store.storage_dir / "array.json"
    invalid.write_text("[]")

    with pytest.raises(ValueError, match="object"):
        run(store.load("array"))

    assert run(store.list_sessions()) == []


def test_list_sessions_returns_metadata_and_skips_malformed_files(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    now = time.time()
    (store.storage_dir / "good.json").write_text(
        json.dumps(
            {
                "name": "display-name",
                "url": "https://example.com",
                "saved_at": now - 7200,
                "saved_at_human": "recently",
                "cookie_count": 2,
                "cookies": [{"value": "must-not-leak"}],
            }
        )
    )
    (store.storage_dir / "defaults.json").write_text("{}")
    (store.storage_dir / "malformed.json").write_text("{")

    sessions = run(store.list_sessions())

    assert [item["name"] for item in sessions] == ["defaults", "display-name"]
    assert sessions[0]["saved_at"] == "unknown"
    assert sessions[1]["age_hours"] == pytest.approx(2.0, abs=0.1)
    assert "cookies" not in sessions[1]


def test_delete_reports_existing_and_missing_sessions(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("saved", []))

    assert run(store.delete("saved")) == "Session 'saved' deleted."
    assert run(store.delete("saved")) == "Session 'saved' not found."
    assert run(store.exists("saved")) is False
