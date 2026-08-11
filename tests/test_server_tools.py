"""MCP tool result, pagination, budget, and safety contracts."""

import asyncio
import base64
import importlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import types as mcp_types


def run(coro):
    return asyncio.run(coro)


def server_module():
    """Import lazily so an SDK import regression appears as a test failure."""
    return importlib.import_module("tor_mcp.server")


def image_blocks(result):
    assert isinstance(result, mcp_types.CallToolResult)
    return [item for item in result.content if isinstance(item, mcp_types.ImageContent)]


def text_blocks(result):
    assert isinstance(result, mcp_types.CallToolResult)
    return [item for item in result.content if isinstance(item, mcp_types.TextContent)]


class _ScreenshotBrowser:
    def __init__(self, image=b"png-data"):
        self.image = image
        self.element_selector = None

    async def screenshot(self, full_page=False):
        return self.image

    async def screenshot_element(self, selector):
        self.element_selector = selector
        return self.image


def test_page_screenshot_returns_native_mcp_image_content(monkeypatch):
    server = server_module()
    browser = _ScreenshotBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)

    result = run(server.tor_screenshot(full_page=True))

    images = image_blocks(result)
    assert len(images) == 1
    assert images[0].mime_type == "image/png"
    assert base64.b64decode(images[0].data) == b"png-data"
    assert text_blocks(result), "image results should retain a short textual description"


def test_element_screenshot_returns_native_mcp_image_content(monkeypatch):
    server = server_module()
    browser = _ScreenshotBrowser(b"element-png")
    monkeypatch.setattr(server, "get_browser", lambda: browser)

    result = run(server.tor_screenshot_element("#challenge"))

    assert browser.element_selector == "#challenge"
    assert base64.b64decode(image_blocks(result)[0].data) == b"element-png"


def test_screenshot_rejects_images_over_the_raw_byte_budget(monkeypatch):
    server = server_module()
    monkeypatch.setattr(server, "MAX_IMAGE_BYTES", 4)
    monkeypatch.setattr(server, "get_browser", lambda: _ScreenshotBrowser(b"12345"))

    with pytest.raises(ValueError, match="(?i)(image|screenshot).*(large|limit|budget)"):
        run(server.tor_screenshot())


class _LaunchOrderingBrowser:
    def __init__(self):
        self.launched = False
        self.events = []
        self._page = object()

    async def ensure_launched(self):
        self.events.append("ensure_launched")
        self.launched = True

    @property
    def page(self):
        assert self.launched, "the tool accessed page before ensure_launched()"
        self.events.append("page")
        return self._page


class _CaptchaRelay:
    def __init__(self, image=b"captcha-image", guess=None):
        self.image = image
        self.guess = guess

    async def get_captcha_image(self, page, selector):
        return {
            "image_base64": base64.b64encode(self.image).decode(),
            "ocr_guess": self.guess,
            "strategy": "vision_relay" if self.guess is None else "local_ocr",
            "instructions": "Read the attached image.",
        }

    async def solve_captcha_auto(self, page, image_selector, input_selector):
        return {
            "image_base64": base64.b64encode(self.image).decode(),
            "ocr_guess": self.guess,
            "strategy": "vision_relay" if self.guess is None else "local_ocr",
            "auto_filled": self.guess is not None,
            "instructions": "Read the attached image.",
        }


def test_get_captcha_launches_browser_then_returns_native_image(monkeypatch):
    server = server_module()
    browser = _LaunchOrderingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "get_captcha", lambda: _CaptchaRelay())

    result = run(server.tor_get_captcha(".captcha"))

    assert browser.events[:2] == ["ensure_launched", "page"]
    assert base64.b64decode(image_blocks(result)[0].data) == b"captcha-image"
    assert text_blocks(result)


def test_solve_captcha_launches_browser_and_keeps_image_when_ocr_fails(monkeypatch):
    server = server_module()
    browser = _LaunchOrderingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "get_captcha", lambda: _CaptchaRelay(guess=None))

    result = run(server.tor_solve_captcha("img.captcha", "#answer"))

    assert browser.events[:2] == ["ensure_launched", "page"]
    assert base64.b64decode(image_blocks(result)[0].data) == b"captcha-image"
    description = "\n".join(block.text for block in text_blocks(result))
    assert "auto_filled" in description
    assert "false" in description.lower()


def test_solve_captcha_returns_native_image_even_with_an_ocr_guess(monkeypatch):
    server = server_module()
    browser = _LaunchOrderingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "get_captcha", lambda: _CaptchaRelay(guess="a7B9"))

    result = run(server.tor_solve_captcha("img", "input"))

    assert base64.b64decode(image_blocks(result)[0].data) == b"captcha-image"


class _ReadingBrowser:
    def __init__(self, count=250):
        self.links = [
            {"text": f"Link {index}", "href": f"https://example.com/{index}"}
            for index in range(count)
        ]
        self.elements = [{"index": index, "text": f"Element {index}"} for index in range(count)]
        self.navigated_to = None

    async def get_html(self):
        return "<html><head><title>Long</title></head><body>" + ("word " * 5000) + "</body></html>"

    async def current_url(self):
        return "https://example.com/long"

    async def get_page_info(self):
        return {"title": "Long", "url": "https://example.com/long"}

    async def get_links(self):
        return self.links

    async def query_elements(self, selector):
        return self.elements

    async def navigate(self, url, timeout=60000):
        self.navigated_to = url
        return {"url": url, "title": "Results", "status": 200}


def test_read_page_honors_requested_character_budget(monkeypatch):
    server = server_module()
    monkeypatch.setattr(server, "get_browser", lambda: _ReadingBrowser())

    result = run(server.tor_read_page(max_chars=120))

    assert len(result) <= 120
    assert result.startswith("[UNTRUSTED WEB CONTENT")


def test_read_page_clamps_excessive_character_budget(monkeypatch):
    server = server_module()
    browser = _ReadingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "MAX_RESPONSE_CHARS", 256)

    result = run(server.tor_read_page(max_chars=10**9))

    assert len(result) <= 256


def test_get_links_supports_offset_limit_and_clamps_limit(monkeypatch):
    server = server_module()
    monkeypatch.setattr(server, "get_browser", lambda: _ReadingBrowser())

    page_result = json.loads(run(server.tor_get_links(offset=10, limit=3)))
    clamped_result = json.loads(run(server.tor_get_links(offset=0, limit=10**9)))
    page = page_result["data"]
    clamped = clamped_result["data"]

    assert page_result["untrusted"] is True
    assert [item["text"] for item in page] == ["Link 10", "Link 11", "Link 12"]
    assert len(clamped) == 100


def test_query_elements_supports_offset_limit_and_clamps_limit(monkeypatch):
    server = server_module()
    monkeypatch.setattr(server, "get_browser", lambda: _ReadingBrowser())

    page = json.loads(run(server.tor_query_elements("a", offset=20, limit=2)))["data"]
    clamped = json.loads(run(server.tor_query_elements("a", limit=10**9)))["data"]

    assert [item["index"] for item in page] == [20, 21]
    assert len(clamped) == 100


class _HtmlBrowser:
    async def get_html(self):
        return "<html></html>"


@pytest.mark.parametrize(
    ("tool_name", "extractor_name", "prefix"),
    [
        ("tor_extract_threads", "extract_forum_threads", "Thread"),
        ("tor_extract_posts", "extract_forum_posts", "Post"),
    ],
)
def test_extraction_tools_paginate_and_clamp(monkeypatch, tool_name, extractor_name, prefix):
    server = server_module()
    items = [{"title": f"{prefix} {index}", "index": index} for index in range(250)]
    monkeypatch.setattr(server, "get_browser", lambda: _HtmlBrowser())
    monkeypatch.setattr(server, extractor_name, lambda html: items)

    page = json.loads(run(getattr(server, tool_name)(offset=5, limit=4)))["data"]
    clamped = json.loads(run(getattr(server, tool_name)(limit=10**9)))["data"]

    assert [item["index"] for item in page] == [5, 6, 7, 8]
    assert len(clamped) == 100


def test_search_percent_encodes_query_and_honors_budgets(monkeypatch):
    server = server_module()
    browser = _ReadingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)

    result = run(server.tor_search("spaces & slash/é?", max_chars=600, limit=2))

    encoded_query = parse_qs(urlsplit(browser.navigated_to).query)["q"][0]
    assert encoded_query == "spaces & slash/é?"
    assert "%26" in browser.navigated_to
    assert "%2F" in browser.navigated_to
    assert "%C3%A9" in browser.navigated_to
    assert len(result) <= 600
    assert result.count("https://example.com/") <= 2


def test_search_clamps_excessive_character_and_item_budgets(monkeypatch):
    server = server_module()
    browser = _ReadingBrowser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "MAX_RESPONSE_CHARS", 700)

    result = run(server.tor_search("bounded", max_chars=10**9, limit=10**9))

    assert len(result) <= 700
    assert result.count("https://example.com/") <= 100


def test_json_tools_bound_a_single_oversized_web_field(monkeypatch):
    server = server_module()
    browser = _ReadingBrowser(count=1)
    browser.links[0]["href"] = "https://example.com/" + ("x" * 10000)
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "MAX_RESPONSE_CHARS", 300)

    result = run(server.tor_get_links())
    payload = json.loads(result)

    assert len(result) <= 300
    assert payload["untrusted"] is True
    assert isinstance(payload["data"], list)


def test_javascript_evaluation_is_denied_unless_explicitly_enabled(monkeypatch):
    server = server_module()

    class Browser:
        def __init__(self):
            self.calls = []

        async def evaluate_js(self, script):
            self.calls.append(script)
            return "evaluated"

    browser = Browser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "ALLOW_JAVASCRIPT", False)

    with pytest.raises(PermissionError, match="(?i)(javascript|allow|disabled)"):
        run(server.tor_evaluate_js("document.cookie"))
    assert browser.calls == []

    monkeypatch.setattr(server, "ALLOW_JAVASCRIPT", True)
    assert run(server.tor_evaluate_js("document.title")) == "evaluated"
    assert browser.calls == ["document.title"]


@pytest.mark.parametrize(
    ("tool_name", "read_only", "destructive", "open_world"),
    [
        ("tor_read_page", True, False, True),
        ("tor_navigate", False, False, True),
        ("tor_evaluate_js", False, True, True),
        ("tor_new_identity", False, True, True),
        ("tor_delete_session", False, True, False),
        ("tor_list_sessions", True, False, False),
        ("tor_search", False, False, True),
    ],
)
def test_tools_publish_safety_annotations(tool_name, read_only, destructive, open_world):
    server = server_module()
    tool = server.mcp._tool_manager._tools[tool_name]

    assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
    assert tool.annotations.read_only_hint is read_only
    assert tool.annotations.destructive_hint is destructive
    assert tool.annotations.open_world_hint is open_world
