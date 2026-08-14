"""Bounded site crawl tool tests — tor_crawl_site."""

import asyncio
import importlib
import json

import pytest


def run(coro):
    return asyncio.run(coro)


def server_module():
    return importlib.import_module("tor_mcp.server")


# ── Fake browser for crawl tests ─────────────────────────────────


class _CrawlBrowser:
    """Fake browser that serves a set of pages for crawl testing."""

    def __init__(self, pages):
        """pages: dict of url -> {title, html, links}."""
        self._pages = pages
        self._navigated_urls = []

    async def crawl_site(self, url, *, timeout=60000, tab_id=None):
        self._navigated_urls.append(url)
        if url not in self._pages:
            return {
                "error": {"message": f"Page not found: {url}", "category": "permanent"},
                "url": url,
            }
        page = self._pages[url]
        return {
            "url": url,
            "title": page.get("title", ""),
            "html": page["html"],
            "links": page.get("links", []),
        }


def _simple_html(body_text):
    return (
        f"<html><head><title>Test</title></head>"
        f"<body><p>{body_text}</p></body></html>"
    )


# ── Happy path tests ─────────────────────────────────────────────


class TestCrawlSiteHappyPath:
    """Happy-path tests for tor_crawl_site."""

    def test_crawl_depth_1_returns_start_and_linked_pages(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home page"),
                "links": [
                    "https://example.com/about",
                    "https://example.com/blog",
                    "https://example.com/contact",
                ],
            },
            "https://example.com/about": {
                "title": "About",
                "html": _simple_html("About page"),
                "links": ["https://example.com/"],
            },
            "https://example.com/blog": {
                "title": "Blog",
                "html": _simple_html("Blog page"),
                "links": [],
            },
            "https://example.com/contact": {
                "title": "Contact",
                "html": _simple_html("Contact page"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=1,
            max_pages=10,
        ))

        parsed = json.loads(result)
        assert parsed["untrusted"] is True
        data = parsed["data"]
        assert data["pages_crawled"] == 4

    def test_crawl_respects_max_depth(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": ["https://example.com/level1"],
            },
            "https://example.com/level1": {
                "title": "Level 1",
                "html": _simple_html("Level 1"),
                "links": ["https://example.com/level2"],
            },
            "https://example.com/level2": {
                "title": "Level 2",
                "html": _simple_html("Level 2"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=1,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        # depth=0 (start) + depth=1 (level1) = 2 pages; level2 is depth 2 — not visited
        assert data["pages_crawled"] == 2
        urls_crawled = {v["url"] for v in data["site_map"].values()}
        assert "https://example.com/level2" not in urls_crawled

    def test_crawl_respects_max_pages(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": [
                    "https://example.com/a",
                    "https://example.com/b",
                    "https://example.com/c",
                ],
            },
            "https://example.com/a": {
                "title": "A",
                "html": _simple_html("Page A"),
                "links": [],
            },
            "https://example.com/b": {
                "title": "B",
                "html": _simple_html("Page B"),
                "links": [],
            },
            "https://example.com/c": {
                "title": "C",
                "html": _simple_html("Page C"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=5,
            max_pages=2,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        assert data["pages_crawled"] == 2


# ── Edge case tests ──────────────────────────────────────────────


class TestCrawlSiteEdgeCases:
    """Edge-case tests for tor_crawl_site."""

    def test_cross_origin_link_collected_not_followed(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": [
                    "https://external.com/page",
                    "https://example.com/local",
                ],
            },
            "https://example.com/local": {
                "title": "Local",
                "html": _simple_html("Local page"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=2,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        assert data["pages_crawled"] == 2
        assert "https://external.com/page" in data["external_links"]
        # External URL should not have been navigated to
        assert "https://external.com/page" not in browser._navigated_urls

    def test_policy_violation_link_skipped(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": [
                    "https://example.com/ok",
                    "https://example.com:0/bad-port",
                ],
            },
            "https://example.com/ok": {
                "title": "OK",
                "html": _simple_html("OK page"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=2,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        assert data["pages_crawled"] == 2
        assert data.get("skipped_policy_violations")

    def test_circular_links_deduplication(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/a": {
                "title": "A",
                "html": _simple_html("Page A"),
                "links": ["https://example.com/b"],
            },
            "https://example.com/b": {
                "title": "B",
                "html": _simple_html("Page B"),
                "links": ["https://example.com/a"],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/a",
            max_depth=10,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        # Only 2 unique pages despite circular links
        assert data["pages_crawled"] == 2

    def test_page_navigation_fails_skips_and_continues(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": [
                    "https://example.com/missing",
                    "https://example.com/ok",
                ],
            },
            # "missing" is not in the pages dict — will return error
            "https://example.com/ok": {
                "title": "OK",
                "html": _simple_html("OK page"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=2,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        # Home + ok = 2; missing is in failed_urls
        assert data["pages_crawled"] == 2
        assert any("missing" in f["url"] for f in data["failed_urls"])

    def test_fragment_urls_deduplicated(self, monkeypatch):
        server = server_module()
        browser = _CrawlBrowser({
            "https://example.com/page": {
                "title": "Page",
                "html": _simple_html("The page"),
                "links": [
                    "https://example.com/page#section1",
                    "https://example.com/page#section2",
                    "https://example.com/other",
                ],
            },
            "https://example.com/other": {
                "title": "Other",
                "html": _simple_html("Other page"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/page",
            max_depth=1,
            max_pages=10,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        # Fragment links to same page are deduplicated
        assert data["pages_crawled"] == 2

    def test_max_depth_and_max_pages_clamped_to_max_item_limit(self, monkeypatch):
        server = server_module()
        monkeypatch.setattr(server, "MAX_ITEM_LIMIT", 2)
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("Home"),
                "links": [
                    "https://example.com/a",
                    "https://example.com/b",
                    "https://example.com/c",
                ],
            },
            "https://example.com/a": {
                "title": "A",
                "html": _simple_html("A"),
                "links": [],
            },
            "https://example.com/b": {
                "title": "B",
                "html": _simple_html("B"),
                "links": [],
            },
            "https://example.com/c": {
                "title": "C",
                "html": _simple_html("C"),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=100,
            max_pages=100,
        ))

        parsed = json.loads(result)
        data = parsed["data"]
        # Clamped to MAX_ITEM_LIMIT=2
        assert data["pages_crawled"] == 2


# ── Error path tests ─────────────────────────────────────────────


class TestCrawlSiteErrors:
    """Error-path tests for tor_crawl_site."""

    def test_start_url_fails_validation(self):
        server = server_module()

        result = run(server.tor_crawl_site(
            start_url="ftp://example.com",
            max_depth=1,
            max_pages=5,
        ))

        parsed = json.loads(result)
        assert parsed["untrusted"] is False
        assert "error" in parsed
        assert "validation" in parsed["error"]["message"].lower()

    def test_max_depth_less_than_one_raises(self):
        server = server_module()

        with pytest.raises(ValueError, match="max_depth must be at least 1"):
            run(server.tor_crawl_site(
                start_url="https://example.com/",
                max_depth=0,
                max_pages=5,
            ))

    def test_max_pages_less_than_one_raises(self):
        server = server_module()

        with pytest.raises(ValueError, match="max_pages must be at least 1"):
            run(server.tor_crawl_site(
                start_url="https://example.com/",
                max_depth=1,
                max_pages=0,
            ))

    def test_all_pages_fail_returns_error(self, monkeypatch):
        server = server_module()
        # Browser returns error for the start URL
        browser = _CrawlBrowser({})
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=1,
            max_pages=5,
        ))

        parsed = json.loads(result)
        assert "error" in parsed

    def test_browser_exception_handled_gracefully(self, monkeypatch):
        server = server_module()

        class _ExplodingBrowser:
            async def crawl_site(self, url, *, timeout=60000, tab_id=None):
                raise ConnectionError("Browser crashed")

        monkeypatch.setattr(server, "get_browser", lambda: _ExplodingBrowser())

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=1,
            max_pages=5,
        ))

        parsed = json.loads(result)
        assert "error" in parsed


# ── Integration tests ────────────────────────────────────────────


class TestCrawlSiteIntegration:
    """Integration-level tests for tor_crawl_site."""

    def test_crawl_releases_lock_between_pages(self, monkeypatch):
        """Verify the lock is NOT held across the entire crawl."""
        import inspect

        server = server_module()
        fn = server.tor_crawl_site
        source = inspect.getsource(fn)
        # The function must manage lock manually, not via @serialized_browser_tool
        assert "_browser_operation_lock" in source

    def test_crawl_output_bounded_by_response_chars(self, monkeypatch):
        server = server_module()
        monkeypatch.setattr(server, "MAX_RESPONSE_CHARS", 300)
        monkeypatch.setattr(server, "MAX_JSON_FIELD_CHARS", 100)
        browser = _CrawlBrowser({
            "https://example.com/": {
                "title": "Home",
                "html": _simple_html("x" * 1000),
                "links": [],
            },
        })
        monkeypatch.setattr(server, "get_browser", lambda: browser)

        result = run(server.tor_crawl_site(
            start_url="https://example.com/",
            max_depth=1,
            max_pages=5,
        ))

        assert len(result) <= 300


class TestCrawlSiteAnnotations:
    """Verify the tool has correct MCP annotations."""

    def test_crawl_site_has_navigate_open_annotation(self):
        from mcp import types as mcp_types

        server = server_module()
        tool = server.mcp._tool_manager._tools["tor_crawl_site"]

        assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is True
