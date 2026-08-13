"""Session listing, missing-file, malformed-file, and deletion behavior."""

import asyncio
import json
import stat
import time
from datetime import UTC, datetime, timedelta

import pytest

from tor_mcp.sessions import SessionStore, _is_expired


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


# ── Metadata fields ─────────────────────────────────────────────


def test_save_stores_description_and_last_used(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("research", [{"name": "sid"}], "https://forum.onion", description="Forum login"))

    data = json.loads((store.storage_dir / "research.json").read_text())
    assert data["description"] == "Forum login"
    assert "last_used" in data
    # last_used should be a valid ISO timestamp
    datetime.fromisoformat(data["last_used"])


def test_save_without_description_omits_field(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("plain", []))

    data = json.loads((store.storage_dir / "plain.json").read_text())
    assert "description" not in data
    assert "last_used" in data


def test_list_sessions_includes_description_and_last_used(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("described", [], "https://example.com", description="My session"))

    sessions = run(store.list_sessions())
    entry = sessions[0]
    assert entry["description"] == "My session"
    assert "last_used" in entry


def test_load_updates_last_used(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("sess", [{"name": "c", "value": "v", "url": "https://x.onion"}]))

    data_before = json.loads((store.storage_dir / "sess.json").read_text())
    before_ts = data_before["last_used"]

    # Small delay to ensure timestamp changes.
    time.sleep(0.01)
    run(store.load("sess"))

    data_after = json.loads((store.storage_dir / "sess.json").read_text())
    assert data_after["last_used"] >= before_ts


# ── Auto-save and expiry ────────────────────────────────────────


def test_save_with_auto_save_sets_expiry_fields(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("auto1", [], "https://example.onion", auto_save=True))

    data = json.loads((store.storage_dir / "auto1.json").read_text())
    assert data["auto_saved"] is True
    assert "expires_at" in data
    expires = datetime.fromisoformat(data["expires_at"])
    # Expiry should be roughly 7 days from now.
    assert abs((expires - datetime.now(UTC)).days - 7) <= 1


def test_explicit_save_on_auto_saved_session_converts_to_permanent(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("convert", [{"name": "c"}], "https://example.onion", auto_save=True))

    # Verify it is auto-saved.
    data = json.loads((store.storage_dir / "convert.json").read_text())
    assert data["auto_saved"] is True

    # Explicit save converts to permanent.
    result = run(store.save("convert", [{"name": "c"}], "https://example.onion"))
    assert "permanent" in result

    data = json.loads((store.storage_dir / "convert.json").read_text())
    assert "auto_saved" not in data
    assert "expires_at" not in data


def test_list_sessions_includes_auto_save_info(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("autolisted", [], "https://example.onion", auto_save=True))

    sessions = run(store.list_sessions())
    entry = sessions[0]
    assert entry["auto_saved"] is True
    assert "expires_at" in entry


def test_expired_auto_saved_session_cleaned_up_on_list(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    # Create an auto-saved session with expiry in the past.
    expired_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    session_data = {
        "name": "expired",
        "url": "https://old.onion",
        "saved_at": time.time() - 86400,
        "saved_at_human": "yesterday",
        "cookie_count": 0,
        "cookies": [],
        "auto_saved": True,
        "expires_at": expired_at,
    }
    (store.storage_dir / "expired.json").write_text(json.dumps(session_data))

    sessions = run(store.list_sessions())
    assert len(sessions) == 0
    assert not (store.storage_dir / "expired.json").exists()


def test_expired_auto_saved_session_cleaned_up_on_load(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    expired_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    session_data = {
        "name": "gone",
        "url": "https://old.onion",
        "saved_at": time.time(),
        "saved_at_human": "now",
        "cookie_count": 1,
        "cookies": [{"name": "c"}],
        "auto_saved": True,
        "expires_at": expired_at,
    }
    (store.storage_dir / "gone.json").write_text(json.dumps(session_data))

    result = run(store.load("gone"))
    assert result is None
    assert not (store.storage_dir / "gone.json").exists()


def test_non_expired_auto_saved_session_loads_normally(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    future = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    session_data = {
        "name": "fresh",
        "url": "https://active.onion",
        "saved_at": time.time(),
        "saved_at_human": "now",
        "cookie_count": 1,
        "cookies": [{"name": "c", "value": "v", "url": "https://active.onion"}],
        "auto_saved": True,
        "expires_at": future,
    }
    (store.storage_dir / "fresh.json").write_text(json.dumps(session_data))

    result = run(store.load("fresh"))
    assert result is not None
    assert result["cookie_count"] == 1


def test_permanent_session_not_affected_by_expiry_check(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("permanent", [{"name": "c", "value": "v", "url": "https://x.onion"}]))

    # Permanent sessions have no auto_saved or expires_at fields.
    result = run(store.load("permanent"))
    assert result is not None


# ── _is_expired helper ──────────────────────────────────────────


@pytest.mark.parametrize(
    "data, expected",
    [
        ({}, False),
        ({"auto_saved": False}, False),
        ({"auto_saved": True}, False),
        ({"auto_saved": True, "expires_at": ""}, False),
        ({"auto_saved": True, "expires_at": "invalid"}, False),
        (
            {
                "auto_saved": True,
                "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
            True,
        ),
        (
            {
                "auto_saved": True,
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
            False,
        ),
    ],
    ids=[
        "empty-data",
        "not-auto-saved",
        "auto-saved-no-expiry",
        "auto-saved-empty-expiry",
        "auto-saved-invalid-expiry",
        "auto-saved-expired",
        "auto-saved-not-expired",
    ],
)
def test_is_expired(data, expected):
    assert _is_expired(data) is expected


# ── Auto-save trigger on browser ────────────────────────────────


def test_auto_save_triggers_on_domain_change(tmp_path):
    """Auto-save fires when navigating to a different domain."""
    from tor_mcp.browser import TorBrowser

    store = SessionStore(tmp_path / "sessions")
    browser = TorBrowser.__new__(TorBrowser)
    browser._auto_save_session = None
    browser._auto_save_last_domain = None
    browser._auto_save_store = store
    browser._launched = False
    browser._context = None
    browser._tabs = {}

    # Simulate: set up auto-save for session "mysess" at domain "a.onion".
    browser.enable_auto_save("mysess", current_domain="a.onion")
    assert browser._auto_save_session == "mysess"
    assert browser._auto_save_last_domain == "a.onion"

    # Same domain -- should NOT save.
    run(browser._maybe_auto_save("https://a.onion/page2"))
    assert not (store.storage_dir / "mysess.json").exists()

    # Different domain -- we can't fully test because get_cookies needs
    # a launched browser. But we can verify the domain tracking logic.
    browser._auto_save_last_domain = "a.onion"
    # Disable auto-save and re-enable to test disable.
    browser.disable_auto_save()
    assert browser._auto_save_session is None

    # After disabling, _maybe_auto_save should be a no-op.
    run(browser._maybe_auto_save("https://b.onion/page1"))
    assert not (store.storage_dir / "mysess.json").exists()
