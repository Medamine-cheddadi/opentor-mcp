"""Tests for unambiguous, private on-disk session persistence."""

import asyncio
import json
import stat

import pytest

from tor_mcp.sessions import SessionStore


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "_leading",
        "-leading",
        "has space",
        "a/b",
        "a.b",
        "../escape",
        "café",
        "a" * 65,
    ],
)
def test_session_names_are_rejected_instead_of_lossily_sanitized(tmp_path, name):
    store = SessionStore(tmp_path / "sessions")

    with pytest.raises(ValueError):
        run(store.save(name, []))

    assert list(store.storage_dir.glob("*.json")) == []


@pytest.mark.parametrize("name", ["a", "forum-1", "forum_one", "A" * 64])
def test_session_names_accept_the_documented_collision_free_grammar(tmp_path, name):
    store = SessionStore(tmp_path / "sessions")

    run(store.save(name, [{"name": "sid", "value": "secret"}]))

    assert run(store.exists(name)) is True
    assert run(store.load(name))["name"] == name


@pytest.mark.parametrize("method_name", ["load", "exists", "delete"])
def test_every_session_operation_validates_names(tmp_path, method_name):
    store = SessionStore(tmp_path / "sessions")

    with pytest.raises(ValueError):
        run(getattr(store, method_name)("a/b"))


def test_session_directory_and_files_are_owner_only(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    run(store.save("private", [{"name": "token", "value": "top-secret"}]))

    directory_mode = stat.S_IMODE(store.storage_dir.stat().st_mode)
    file_mode = stat.S_IMODE((store.storage_dir / "private.json").stat().st_mode)

    assert directory_mode == 0o700
    assert file_mode == 0o600


def test_existing_overly_permissive_session_directory_is_hardened(tmp_path):
    storage = tmp_path / "sessions"
    storage.mkdir(mode=0o755)
    storage.chmod(0o755)

    SessionStore(storage)

    assert stat.S_IMODE(storage.stat().st_mode) == 0o700


def test_load_rejects_a_symlinked_session_file(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"name": "linked", "cookie_count": 1, "cookies": [{"value": "secret"}]}),
        encoding="utf-8",
    )
    (store.storage_dir / "linked.json").symlink_to(outside)

    with pytest.raises(ValueError, match="(?i)symlink"):
        run(store.load("linked"))


def test_list_sessions_skips_symlinked_json_files(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"name": "leaked", "cookie_count": 1, "cookies": [{"value": "secret"}]}),
        encoding="utf-8",
    )
    (store.storage_dir / "linked.json").symlink_to(outside)

    assert run(store.list_sessions()) == []
