"""Persistent session/cookie storage for Tor browser sessions."""

import asyncio
import json
import logging
import os
import re
import stat
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("tor-mcp.sessions")
SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_session_name(name: str) -> str:
    """Validate a collision-free session identifier."""
    if not SESSION_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            "Invalid session name. Use 1-64 ASCII letters, numbers, '-' or '_', "
            "starting with a letter or number."
        )
    return name


def _write_private_json(path: Path, data: dict) -> None:
    """Atomically replace a session file with owner-only permissions."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(data, handle, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _read_private_json(path: Path) -> dict:
    """Read a regular JSON file without following a final symlink."""
    if path.is_symlink():
        raise ValueError("Session files must not be symlinks.")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise ValueError("Session files must not be symlinks.") from exc
        raise

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Session path must be a regular file.")
        with os.fdopen(descriptor, encoding="utf-8", closefd=False) as handle:
            data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("Session JSON must contain an object.")
            return data
    finally:
        os.close(descriptor)


class SessionStore:
    """Save and restore browser sessions (cookies + metadata) to disk.

    Sessions persist across MCP restarts so you don't need to re-login
    to forums. A saved session includes cookies, the URL it was saved from,
    and a timestamp.
    """

    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or Path("sessions")
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.storage_dir.chmod(0o700)
        for session_file in self.storage_dir.glob("*.json"):
            if session_file.is_file() and not session_file.is_symlink():
                session_file.chmod(0o600)

    def _session_path(self, name: str) -> Path:
        return self.storage_dir / f"{validate_session_name(name)}.json"

    async def save(self, name: str, cookies: list[dict], url: str = "") -> str:
        """Save a session (cookies + metadata) to disk."""
        session_data = {
            "name": name,
            "url": url,
            "saved_at": time.time(),
            "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "cookie_count": len(cookies),
            "cookies": cookies,
        }

        path = self._session_path(name)
        await asyncio.to_thread(_write_private_json, path, session_data)
        logger.info("Session '%s' saved (%d cookies) to %s", name, len(cookies), path)
        return f"Session '{name}' saved with {len(cookies)} cookies."

    async def load(self, name: str) -> dict | None:
        """Load a saved session. Returns None if not found."""
        path = self._session_path(name)
        if path.is_symlink():
            raise ValueError("Session files must not be symlinks.")
        if not path.exists():
            logger.warning("Session '%s' not found at %s", name, path)
            return None

        data = await asyncio.to_thread(_read_private_json, path)
        age_hours = (time.time() - data.get("saved_at", 0)) / 3600
        data["age_hours"] = round(age_hours, 1)
        logger.info(
            "Session '%s' loaded (%d cookies, %.1fh old)",
            name,
            data["cookie_count"],
            age_hours,
        )
        return data

    async def list_sessions(self) -> list[dict]:
        """List all saved sessions with metadata."""
        sessions = []
        for path in sorted(self.storage_dir.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                data = await asyncio.to_thread(_read_private_json, path)
                age_hours = (time.time() - data.get("saved_at", 0)) / 3600
                sessions.append(
                    {
                        "name": data.get("name", path.stem),
                        "url": data.get("url", ""),
                        "saved_at": data.get("saved_at_human", "unknown"),
                        "age_hours": round(age_hours, 1),
                        "cookie_count": data.get("cookie_count", 0),
                    }
                )
            except (json.JSONDecodeError, KeyError, OSError, ValueError):
                continue
        return sessions

    async def delete(self, name: str) -> str:
        """Delete a saved session."""
        path = self._session_path(name)
        if path.exists():
            path.unlink()
            return f"Session '{name}' deleted."
        return f"Session '{name}' not found."

    async def exists(self, name: str) -> bool:
        """Check if a session exists."""
        return self._session_path(name).exists()
