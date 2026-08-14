"""Basic import and structure tests for OpenTor MCP."""

from mcp.server.mcpserver.server import MCPServer


def test_browser_imports():
    from tor_mcp.browser import STEALTH_PREFS, TorBrowser


def test_captcha_imports():
    from tor_mcp.captcha import CaptchaSolver


def test_session_imports():
    from tor_mcp.sessions import SessionStore


def test_extraction_imports():
    from tor_mcp.extraction import (
        classify_page,
        extract_forum_posts,
        extract_forum_threads,
        extract_metadata,
        html_to_markdown,
    )

    assert callable(classify_page)


def test_server_imports():
    from tor_mcp.server import mcp


def test_tool_count():
    from tor_mcp.server import mcp

    tools = mcp._tool_manager._tools
    assert len(tools) == 37


def test_privacy_prefs():
    from tor_mcp.browser import STEALTH_PREFS

    assert STEALTH_PREFS["privacy.resistFingerprinting"] is True
    assert STEALTH_PREFS["network.proxy.socks_remote_dns"] is True
    assert "general.platform.override" not in STEALTH_PREFS
    assert STEALTH_PREFS["webgl.disabled"] is True


def test_extraction():
    from tor_mcp.extraction import extract_metadata, html_to_markdown

    html = "<html><head><title>Test</title></head><body><p>Hello</p></body></html>"
    md = html_to_markdown(html)
    assert "Hello" in md
    meta = extract_metadata(html)
    assert meta["title"] == "Test"


def test_session_store():
    import tempfile
    from pathlib import Path

    from tor_mcp.sessions import SessionStore

    with tempfile.TemporaryDirectory() as td:
        store = SessionStore(storage_dir=Path(td))
        assert store.storage_dir.exists()
