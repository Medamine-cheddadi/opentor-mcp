"""Auto-pagination tool tests — detect_pagination + tor_auto_paginate."""

import asyncio
import importlib

import pytest


def run(coro):
    return asyncio.run(coro)


def server_module():
    return importlib.import_module("tor_mcp.server")


# ── detect_pagination unit tests ──────────────────────────────────


class TestDetectPagination:
    """Tests for the detect_pagination helper in extraction.py."""

    def test_link_rel_next_is_highest_priority(self):
        from tor_mcp.extraction import detect_pagination

        html = """
        <html><head>
          <link rel="next" href="/page/2">
        </head><body>
          <a href="/other">Next</a>
        </body></html>
        """
        assert detect_pagination(html) == "/page/2"

    def test_anchor_text_next(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/page/2">Next</a></body></html>'
        assert detect_pagination(html) == "/page/2"

    def test_anchor_text_next_page(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/p2">Next Page</a></body></html>'
        assert detect_pagination(html) == "/p2"

    def test_anchor_text_arrow_right(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/p3">→</a></body></html>'
        assert detect_pagination(html) == "/p3"

    def test_anchor_text_double_angle(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/p4">»</a></body></html>'
        assert detect_pagination(html) == "/p4"

    def test_anchor_text_next_arrow(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/p5">Next →</a></body></html>'
        assert detect_pagination(html) == "/p5"

    def test_anchor_rel_next(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a rel="next" href="/p6">Page 2</a></body></html>'
        assert detect_pagination(html) == "/p6"

    def test_no_pagination_signals(self):
        from tor_mcp.extraction import detect_pagination

        html = "<html><body><p>No links here.</p></body></html>"
        assert detect_pagination(html) is None

    def test_empty_html(self):
        from tor_mcp.extraction import detect_pagination

        assert detect_pagination("") is None
        assert detect_pagination(None) is None

    def test_non_matching_link_text(self):
        from tor_mcp.extraction import detect_pagination

        html = '<html><body><a href="/about">About us</a></body></html>'
        assert detect_pagination(html) is None


# ── Fake browser for auto-paginate tests ─────────────────────────


class _PaginatingBrowser:
    """Fake browser that serves a sequence of pages with next-page links."""

    def __init__(self, pages):
        """pages: list of dicts with keys: url, title, html, next_url (optional)."""
        self._pages = pages
        self._current = 0
        self._navigated_urls = []

    async def get_page_info(self, tab_id=None):
        page = self._pages[self._current]
        return {"url": page["url"], "title": page.get("title", "Page")}

    async def get_html(self, tab_id=None):
        return self._pages[self._current]["html"]

    async def navigate(self, url, timeout=60000, **kwargs):
        self._navigated_urls.append(url)
        # Find the page matching this URL
        for i, page in enumerate(self._pages):
            if page["url"] == url:
                self._current = i
                return {"url": url, "title": page.get("title", "Page"), "status": 200}
        # URL not found in our pages — simulate navigation error
        return {"error": {"message": f"Page not found: {url}", "category": "permanent"}}


def _make_html(body_text, next_url=None):
    """Build a simple HTML page with optional next-page link."""
    next_link = ""
    if next_url:
        next_link = f'<a href="{next_url}">Next</a>'
    return (
        f"<html><head><title>Test</title></head>"
        f"<body><p>{body_text}</p>{next_link}</body></html>"
    )


# ── tor_auto_paginate tool tests ─────────────────────────────────


class TestAutoPaginateHappyPath:
    """Happy-path tests for tor_auto_paginate."""

    def test_follows_link_rel_next_and_aggregates(self, monkeypatch):
        server = server_module()
        html1 = (
            '<html><head><link rel="next" href="https://example.com/page/2">'
            "</head><body><p>Page one content here.</p></body></html>"
        )
        html2 = "<html><body><p>Page two content here.</p></body></html>"
        browser = _PaginatingBrowser([
            {"url": "https://example.com/page/1", "title": "P1", "html": html1},
            {"url": "https://example.com/page/2", "title": "P2", "html": html2},
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=5))

        assert "Page one content" in result
        assert "Page two content" in result
        assert "2 page(s) collected" in result
        assert result.startswith("[UNTRUSTED WEB CONTENT")

    def test_follows_text_next_link(self, monkeypatch):
        server = server_module()
        browser = _PaginatingBrowser([
            {
                "url": "https://example.com/1",
                "html": _make_html("First page", "https://example.com/2"),
            },
            {
                "url": "https://example.com/2",
                "html": _make_html("Second page"),
            },
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=10))

        assert "First page" in result
        assert "Second page" in result
        assert "2 page(s) collected" in result


class TestAutoPaginateEdgeCases:
    """Edge-case tests for tor_auto_paginate."""

    def test_no_pagination_signals_returns_single_page(self, monkeypatch):
        server = server_module()
        browser = _PaginatingBrowser([
            {
                "url": "https://example.com/single",
                "html": "<html><body><p>Only page content.</p></body></html>",
            },
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=5))

        assert "Only page content" in result
        assert "1 page(s) collected" in result

    def test_duplicate_url_stops_pagination(self, monkeypatch):
        server = server_module()
        # Page 1 has a next link that points back to itself
        html = _make_html("Looping page", "https://example.com/loop")
        browser = _PaginatingBrowser([
            {"url": "https://example.com/loop", "html": html},
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=10))

        assert "1 page(s) collected" in result
        assert "Duplicate URL" in result

    def test_duplicate_content_hash_stops_pagination(self, monkeypatch):
        server = server_module()
        # Two different URLs serving identical HTML (including the next link)
        # so extracted content fingerprints match
        identical_html = (
            '<html><body><p>Identical content on every page</p>'
            '<a href="https://example.com/page/2">Next</a></body></html>'
        )
        browser = _PaginatingBrowser([
            {"url": "https://example.com/page/1", "html": identical_html},
            {"url": "https://example.com/page/2", "html": identical_html},
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=10))

        assert "Duplicate content" in result

    def test_max_pages_reached_stops_with_note(self, monkeypatch):
        server = server_module()
        browser = _PaginatingBrowser([
            {
                "url": f"https://example.com/{i}",
                "html": _make_html(
                    f"Page {i} content", f"https://example.com/{i + 1}"
                ),
            }
            for i in range(1, 6)
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=3))

        assert "3 page(s) collected" in result
        assert "max_pages limit (3) reached" in result

    def test_response_budget_exceeded_truncates(self, monkeypatch):
        server = server_module()
        # Set a very tight budget
        monkeypatch.setattr(server, "MAX_RESPONSE_CHARS", 500)
        browser = _PaginatingBrowser([
            {
                "url": f"https://example.com/{i}",
                "html": _make_html(
                    "x" * 300, f"https://example.com/{i + 1}"
                ),
            }
            for i in range(1, 10)
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=10))

        assert len(result) <= 500

    def test_max_pages_clamped_to_max_item_limit(self, monkeypatch):
        server = server_module()
        monkeypatch.setattr(server, "MAX_ITEM_LIMIT", 2)
        browser = _PaginatingBrowser([
            {
                "url": f"https://example.com/{i}",
                "html": _make_html(
                    f"Page {i}", f"https://example.com/{i + 1}"
                ),
            }
            for i in range(1, 10)
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=100))

        assert "2 page(s) collected" in result


class TestAutoPaginateErrors:
    """Error-path tests for tor_auto_paginate."""

    def test_navigation_failure_returns_partial_content(self, monkeypatch):
        server = server_module()
        # Page 1 links to page 2 which doesn't exist in our browser
        browser = _PaginatingBrowser([
            {
                "url": "https://example.com/1",
                "html": _make_html("Good content", "https://example.com/missing"),
            },
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=5))

        assert "Good content" in result
        assert "1 page(s) collected" in result
        assert "Navigation to page 2 failed" in result

    def test_max_pages_less_than_one_raises(self):
        server = server_module()

        with pytest.raises(ValueError, match="max_pages must be at least 1"):
            run(server.tor_auto_paginate(max_pages=0))

    def test_browser_exception_returns_partial_content(self, monkeypatch):
        server = server_module()

        class _FailingBrowser:
            def __init__(self):
                self._call_count = 0

            async def get_page_info(self, tab_id=None):
                self._call_count += 1
                if self._call_count > 1:
                    raise ConnectionError("Browser crashed")
                return {"url": "https://example.com/1", "title": "P1"}

            async def get_html(self, tab_id=None):
                return _make_html("First page content", "https://example.com/2")

            async def navigate(self, url, timeout=60000, **kwargs):
                return {"url": url, "title": "P2", "status": 200}

        monkeypatch.setattr(server, "get_browser", lambda: _FailingBrowser())

        result = run(server.tor_auto_paginate(max_pages=5))

        # First page should be collected, error on second
        assert "First page content" in result
        assert "Returning content collected so far" in result


class TestAutoPaginateAnnotations:
    """Verify the tool has correct MCP annotations."""

    def test_auto_paginate_has_navigate_open_annotation(self):
        from mcp import types as mcp_types

        server = server_module()
        tool = server.mcp._tool_manager._tools["tor_auto_paginate"]

        assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is True

    def test_auto_paginate_does_not_use_serialized_decorator(self):
        """The tool should NOT use @serialized_browser_tool — it manages the lock manually."""
        import inspect

        server = server_module()
        fn = server.tor_auto_paginate
        source = inspect.getsource(fn)
        # The wrapper added by @serialized_browser_tool captures the lock at the top level;
        # the raw function should manage it via `async with _browser_operation_lock`
        assert "_browser_operation_lock" in source


class TestAutoPaginateRelativeUrls:
    """Test that relative next-page URLs are resolved correctly."""

    def test_relative_next_url_resolved(self, monkeypatch):
        server = server_module()
        html1 = _make_html("Page one", "/page/2")
        html2 = "<html><body><p>Page two</p></body></html>"
        browser = _PaginatingBrowser([
            {"url": "https://example.com/page/1", "html": html1},
            {"url": "https://example.com/page/2", "html": html2},
        ])
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_auto_paginate(max_pages=5))

        assert "Page one" in result
        assert "Page two" in result
        assert browser._navigated_urls == ["https://example.com/page/2"]
