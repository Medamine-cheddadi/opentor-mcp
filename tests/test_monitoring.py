"""Page monitoring and comparison tool tests — tor_monitor_page, tor_compare_pages."""

import asyncio
import importlib
import json

import pytest


def run(coro):
    return asyncio.run(coro)


def server_module():
    return importlib.import_module("tor_mcp.server")


# ── Fake browser for monitoring tests ──────────────────────────


class _MonitorBrowser:
    """Fake browser that serves configurable page content for monitoring."""

    def __init__(self, content="Hello world", title="Test Page", url="https://example.com"):
        self._content = content
        self._title = title
        self._url = url
        self._launched = True
        self._tabs = {"main": object()}
        self._active_tab = "main"

    def get_page(self, tab_id=None):
        target = tab_id or self._active_tab
        if target not in self._tabs:
            available = ", ".join(sorted(self._tabs))
            raise ValueError(f"Tab '{target}' does not exist. Available tabs: {available}")
        return self._tabs[target]

    async def ensure_launched(self):
        pass

    async def get_page_info(self, tab_id=None):
        return {"url": self._url, "title": self._title}

    async def get_html(self, tab_id=None):
        return (
            f"<html><head><title>{self._title}</title></head>"
            f"<body><p>{self._content}</p></body></html>"
        )

    async def snapshot_page(self, name, snapshots_dir, *, max_snapshots=5, tab_id=None):
        """Delegate to the real TorBrowser.snapshot_page implementation."""
        from tor_mcp.browser import TorBrowser

        return await TorBrowser.snapshot_page(
            self, name, snapshots_dir,
            max_snapshots=max_snapshots, tab_id=tab_id,
        )

    def set_content(self, content):
        self._content = content


class _CompareBrowser:
    """Fake browser with multiple tabs for comparison tests."""

    def __init__(self, tabs_content):
        """tabs_content: dict of tab_id -> {content, title, url}."""
        self._tabs_content = tabs_content
        self._tabs = {tid: object() for tid in tabs_content}
        self._active_tab = next(iter(tabs_content))
        self._launched = True
        self._navigated = []

    def get_page(self, tab_id=None):
        target = tab_id or self._active_tab
        if target not in self._tabs:
            available = ", ".join(sorted(self._tabs))
            raise ValueError(f"Tab '{target}' does not exist. Available tabs: {available}")
        return self._tabs[target]

    async def ensure_launched(self):
        pass

    async def get_html(self, tab_id=None):
        target = tab_id or self._active_tab
        self.get_page(target)  # Validate tab exists.
        info = self._tabs_content[target]
        return (
            f"<html><head><title>{info['title']}</title></head>"
            f"<body><p>{info['content']}</p></body></html>"
        )

    async def get_page_info(self, tab_id=None):
        target = tab_id or self._active_tab
        self.get_page(target)  # Validate tab exists.
        info = self._tabs_content[target]
        return {"url": info["url"], "title": info["title"]}

    async def navigate(self, url, timeout=60000, **kwargs):
        self._navigated.append(url)
        # Find a tab with matching URL and switch to it.
        for tid, info in self._tabs_content.items():
            if info["url"] == url:
                self._active_tab = tid
                return {"url": url, "title": info["title"], "status": 200}
        return {"error": {"message": f"Page not found: {url}", "category": "permanent"}}


def _simple_html(body_text):
    return (
        f"<html><head><title>Test</title></head>"
        f"<body><p>{body_text}</p></body></html>"
    )


# ── tor_monitor_page — happy path ──────────────────────────────


class TestMonitorPageHappyPath:
    """Happy-path tests for tor_monitor_page."""

    def test_first_call_captures_baseline(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser(content="Initial content")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        result = run(server.tor_monitor_page(name="test_page"))

        parsed = json.loads(result)
        assert parsed["status"] == "baseline_captured"
        assert "No previous snapshot" in parsed["message"]
        assert parsed["url"] == "https://example.com"

    def test_second_call_with_changes_returns_diff(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser(content="Version one")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        # First call — baseline
        run(server.tor_monitor_page(name="tracked"))

        # Modify content
        browser.set_content("Version two with extra data")

        # Second call — should detect changes
        result = run(server.tor_monitor_page(name="tracked"))

        parsed = json.loads(result)
        assert parsed["status"] == "changes_detected"
        assert parsed["added_lines"] > 0 or parsed["removed_lines"] > 0
        assert parsed["change_percentage"] > 0
        assert "diff_text" in parsed

    def test_second_call_no_changes_reports_no_changes(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser(content="Stable content")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        # First call — baseline
        run(server.tor_monitor_page(name="stable"))

        # Second call — same content
        result = run(server.tor_monitor_page(name="stable"))

        parsed = json.loads(result)
        assert parsed["status"] == "no_changes"
        assert "No changes" in parsed["message"]


# ── tor_monitor_page — edge cases ──────────────────────────────


class TestMonitorPageEdgeCases:
    """Edge-case tests for tor_monitor_page."""

    def test_max_snapshots_rotation(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser(content="v0")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)
        monkeypatch.setattr(server, "TOR_MAX_SNAPSHOTS", 3)

        for i in range(5):
            browser.set_content(f"Version {i}")
            run(server.tor_monitor_page(name="rotating"))

        # Should only have 3 snapshots remaining
        snap_dir = tmp_path / "rotating"
        remaining = list(snap_dir.glob("*.json"))
        assert len(remaining) == 3

    def test_invalid_name_raises(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser()
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        with pytest.raises(ValueError, match="Invalid"):
            run(server.tor_monitor_page(name="../escape"))

    def test_symlink_directory_rejected(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser()
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        # Create a symlink where the snapshot dir would go
        target = tmp_path / "real_dir"
        target.mkdir()
        link = tmp_path / "linked"
        link.symlink_to(target)

        with pytest.raises(ValueError, match="symlink"):
            run(server.tor_monitor_page(name="linked"))


# ── tor_monitor_page — error paths ─────────────────────────────


class TestMonitorPageErrors:
    """Error-path tests for tor_monitor_page."""

    def test_invalid_tab_id_raises(self, monkeypatch, tmp_path):
        server = server_module()
        browser = _MonitorBrowser()
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        with pytest.raises(ValueError, match="does not exist"):
            run(server.tor_monitor_page(name="ok", tab_id="nonexistent"))


# ── tor_compare_pages — happy path ─────────────────────────────


class TestComparePagesHappyPath:
    """Happy-path tests for tor_compare_pages."""

    def test_compare_two_tabs(self, monkeypatch):
        server = server_module()
        browser = _CompareBrowser({
            "tab1": {
                "content": "Alpha content line one",
                "title": "Page A",
                "url": "https://example.com/a",
            },
            "tab2": {
                "content": "Beta content line two",
                "title": "Page B",
                "url": "https://example.com/b",
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_compare_pages(tab_id_a="tab1", tab_id_b="tab2"))

        parsed = json.loads(result)
        assert "page_a" in parsed
        assert "page_b" in parsed
        assert parsed["page_a"]["url"] == "https://example.com/a"
        assert parsed["page_b"]["url"] == "https://example.com/b"
        assert "added_lines" in parsed
        assert "removed_lines" in parsed
        assert "change_percentage" in parsed
        assert "diff_text" in parsed

    def test_compare_identical_tabs(self, monkeypatch):
        server = server_module()
        browser = _CompareBrowser({
            "tab1": {
                "content": "Same content",
                "title": "Same",
                "url": "https://example.com/a",
            },
            "tab2": {
                "content": "Same content",
                "title": "Same",
                "url": "https://example.com/b",
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_compare_pages(tab_id_a="tab1", tab_id_b="tab2"))

        parsed = json.loads(result)
        assert parsed["added_lines"] == 0
        assert parsed["removed_lines"] == 0
        assert parsed["change_percentage"] == 0.0

    def test_compare_two_urls(self, monkeypatch):
        server = server_module()
        browser = _CompareBrowser({
            "url_a_tab": {
                "content": "First URL content",
                "title": "First",
                "url": "https://example.com/first",
            },
            "url_b_tab": {
                "content": "Second URL content",
                "title": "Second",
                "url": "https://example.com/second",
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_compare_pages(
            url_a="https://example.com/first",
            url_b="https://example.com/second",
        ))

        parsed = json.loads(result)
        assert parsed["page_a"]["url"] == "https://example.com/first"
        assert parsed["page_b"]["url"] == "https://example.com/second"
        assert parsed["added_lines"] > 0 or parsed["removed_lines"] > 0


# ── tor_compare_pages — edge cases ─────────────────────────────


class TestComparePagesEdgeCases:
    """Edge-case tests for tor_compare_pages."""

    def test_no_params_returns_error(self):
        server = server_module()

        result = run(server.tor_compare_pages())

        parsed = json.loads(result)
        assert "error" in parsed

    def test_invalid_tab_id_raises(self, monkeypatch):
        server = server_module()
        browser = _CompareBrowser({
            "tab1": {
                "content": "Content",
                "title": "Page",
                "url": "https://example.com",
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        with pytest.raises(ValueError, match="does not exist"):
            run(server.tor_compare_pages(tab_id_a="tab1", tab_id_b="nonexistent"))

    def test_url_navigation_failure_returns_error(self, monkeypatch):
        server = server_module()
        browser = _CompareBrowser({
            "tab": {
                "content": "Content",
                "title": "Page",
                "url": "https://example.com/exists",
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_compare_pages(
            url_a="https://example.com/missing",
            url_b="https://example.com/exists",
        ))

        parsed = json.loads(result)
        assert "error" in parsed


# ── Integration tests ───────────────────────────────────────────


class TestMonitorPageIntegration:
    """Integration tests for page monitoring."""

    def test_monitor_modify_monitor_detects_changes(self, monkeypatch, tmp_path):
        """Monitor page, modify content, monitor again -> detects changes."""
        server = server_module()
        browser = _MonitorBrowser(content="Original text on the page")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        # First monitor — baseline
        result1 = run(server.tor_monitor_page(name="integration"))
        parsed1 = json.loads(result1)
        assert parsed1["status"] == "baseline_captured"

        # Modify content
        browser.set_content("Updated text with new information added")

        # Second monitor — should detect changes
        result2 = run(server.tor_monitor_page(name="integration"))
        parsed2 = json.loads(result2)
        assert parsed2["status"] == "changes_detected"
        assert parsed2["added_lines"] > 0 or parsed2["removed_lines"] > 0

    def test_snapshot_files_have_secure_permissions(self, monkeypatch, tmp_path):
        """Snapshot directory and files use restrictive permissions."""
        import stat

        server = server_module()
        browser = _MonitorBrowser(content="Secure content")
        monkeypatch.setattr(server, "get_browser", lambda: browser)
        monkeypatch.setattr(server, "SNAPSHOTS_DIR", tmp_path)

        run(server.tor_monitor_page(name="secure"))

        snap_dir = tmp_path / "secure"
        assert snap_dir.is_dir()
        dir_mode = stat.S_IMODE(snap_dir.stat().st_mode)
        assert dir_mode == 0o700

        snap_files = list(snap_dir.glob("*.json"))
        assert len(snap_files) == 1
        file_mode = stat.S_IMODE(snap_files[0].stat().st_mode)
        assert file_mode == 0o600


# ── Annotation tests ────────────────────────────────────────────


class TestMonitoringAnnotations:
    """Verify the tools have correct MCP annotations."""

    def test_monitor_page_has_mutate_local_annotation(self):
        from mcp import types as mcp_types

        server = server_module()
        tool = server.mcp._tool_manager._tools["tor_monitor_page"]

        assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False

    def test_compare_pages_has_read_only_open_annotation(self):
        from mcp import types as mcp_types

        server = server_module()
        tool = server.mcp._tool_manager._tools["tor_compare_pages"]

        assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
