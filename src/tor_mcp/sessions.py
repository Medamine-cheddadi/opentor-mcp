"""Persistent session/cookie storage for Tor browser sessions."""

import asyncio
import json
import logging
import os
import re
import stat
import tempfile
import time
from datetime import UTC, datetime, timedelta
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


def _is_expired(data: dict) -> bool:
    """Return True if an auto-saved session has passed its expiry date."""
    if not data.get("auto_saved"):
        return False
    expires_at_raw = data.get("expires_at")
    if not expires_at_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
        return datetime.now(UTC) >= expires_at
    except (ValueError, TypeError):
        return False


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

    async def save(
        self,
        name: str,
        cookies: list[dict],
        url: str = "",
        *,
        description: str | None = None,
        auto_save: bool = False,
    ) -> str:
        """Save a session (cookies + metadata) to disk.

        When *auto_save* is ``True`` the session is tagged with an expiry
        date (7 days from now).  An explicit ``save()`` call on an
        already-auto-saved session converts it to permanent by removing
        the expiry fields.
        """
        now = datetime.now(UTC)
        path = self._session_path(name)

        # If this is an explicit save on an existing auto-saved session,
        # convert it to permanent by stripping the auto-save fields.
        existing_auto_saved = False
        if not auto_save and path.exists() and not path.is_symlink():
            try:
                existing = await asyncio.to_thread(_read_private_json, path)
                existing_auto_saved = existing.get("auto_saved", False)
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        session_data: dict = {
            "name": name,
            "url": url,
            "saved_at": time.time(),
            "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "last_used": now.isoformat(),
            "cookie_count": len(cookies),
            "cookies": cookies,
        }
        if description is not None:
            session_data["description"] = description
        if auto_save:
            session_data["auto_saved"] = True
            expires_at = now + timedelta(days=7)
            session_data["expires_at"] = expires_at.isoformat()

        await asyncio.to_thread(_write_private_json, path, session_data)
        logger.info("Session '%s' saved (%d cookies) to %s", name, len(cookies), path)
        if existing_auto_saved:
            return f"Session '{name}' saved with {len(cookies)} cookies (converted to permanent)."
        return f"Session '{name}' saved with {len(cookies)} cookies."

    async def load(self, name: str) -> dict | None:
        """Load a saved session. Returns None if not found or expired."""
        path = self._session_path(name)
        if path.is_symlink():
            raise ValueError("Session files must not be symlinks.")
        if not path.exists():
            logger.warning("Session '%s' not found at %s", name, path)
            return None

        data = await asyncio.to_thread(_read_private_json, path)

        # Clean up expired auto-saved sessions on access.
        if _is_expired(data):
            path.unlink(missing_ok=True)
            logger.info("Expired auto-saved session '%s' cleaned up on load", name)
            return None

        age_hours = (time.time() - data.get("saved_at", 0)) / 3600
        data["age_hours"] = round(age_hours, 1)

        # Update last_used timestamp.
        data["last_used"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(_write_private_json, path, data)

        logger.info(
            "Session '%s' loaded (%d cookies, %.1fh old)",
            name,
            data["cookie_count"],
            age_hours,
        )
        return data

    async def list_sessions(self) -> list[dict]:
        """List all saved sessions with metadata.

        Expired auto-saved sessions are deleted during listing and
        excluded from the result.
        """
        sessions = []
        for path in sorted(self.storage_dir.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                data = await asyncio.to_thread(_read_private_json, path)

                # Clean up expired auto-saved sessions.
                if _is_expired(data):
                    path.unlink(missing_ok=True)
                    logger.info(
                        "Expired auto-saved session '%s' cleaned up during listing",
                        data.get("name", path.stem),
                    )
                    continue

                age_hours = (time.time() - data.get("saved_at", 0)) / 3600
                entry: dict = {
                    "name": data.get("name", path.stem),
                    "url": data.get("url", ""),
                    "saved_at": data.get("saved_at_human", "unknown"),
                    "age_hours": round(age_hours, 1),
                    "cookie_count": data.get("cookie_count", 0),
                }
                if data.get("description"):
                    entry["description"] = data["description"]
                if data.get("last_used"):
                    entry["last_used"] = data["last_used"]
                if data.get("auto_saved"):
                    entry["auto_saved"] = True
                    entry["expires_at"] = data.get("expires_at", "")
                sessions.append(entry)
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
