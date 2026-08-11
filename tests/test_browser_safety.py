"""Security and lifecycle tests for the Tor browser wrapper."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tor_mcp import browser as browser_module
from tor_mcp.browser import TorBrowser


def run(coro):
    """Run an async unit under pytest without requiring pytest-asyncio."""
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "example.com",
        "//example.com/path",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "https:///missing-host",
        "https://user:password@example.com/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://172.16.4.5/",
        "http://192.168.1.2/",
        "http://169.254.10.20/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://127.1/",
        "http://127.0.1/",
        "http://0300.0250.0001.0001/",
        "http://3232235777/",
        "http://１27.0.0.1/",
        "http://127。0。0。1/",
        "http://ⓛocalhost/",
    ],
)
def test_navigate_rejects_unsafe_or_malformed_urls_before_launch(tmp_path, url):
    browser = TorBrowser(archives_dir=tmp_path)
    browser.ensure_launched = AsyncMock()

    with pytest.raises(ValueError):
        run(browser.navigate(url))

    browser.ensure_launched.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path?q=value",
        "http://example.onion/",
        "https://check.torproject.org/api/ip",
    ],
)
def test_navigate_accepts_http_urls_with_public_or_onion_hosts(tmp_path, url):
    class Page:
        def __init__(self):
            self.url = url

        async def goto(self, target, **kwargs):
            assert target == url
            return type("Response", (), {"status": 200})()

        async def wait_for_timeout(self, milliseconds):
            return None

        async def title(self):
            return "Safe"

    browser = TorBrowser(archives_dir=tmp_path)
    browser._launched = True
    browser._page = Page()

    assert run(browser.navigate(url))["status"] == 200


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../escape", "nested/name", "name.json"],
)
def test_archive_rejects_names_that_are_not_single_safe_components(tmp_path, name):
    browser = TorBrowser(archives_dir=tmp_path / "archives")
    browser.ensure_launched = AsyncMock()

    with pytest.raises(ValueError):
        run(browser.archive_page(name))

    browser.ensure_launched.assert_not_awaited()


def test_archive_rejects_an_absolute_path(tmp_path):
    archive_root = tmp_path / "archives"
    absolute_target = tmp_path / "outside-target"
    browser = TorBrowser(archives_dir=archive_root)
    browser.ensure_launched = AsyncMock()

    with pytest.raises(ValueError):
        run(browser.archive_page(str(absolute_target)))

    browser.ensure_launched.assert_not_awaited()


def test_archive_rejects_a_symlink_that_escapes_the_archive_root(tmp_path):
    archive_root = tmp_path / "archives"
    outside = tmp_path / "outside"
    outside.mkdir()
    archive_root.mkdir()
    (archive_root / "linked").symlink_to(outside, target_is_directory=True)
    browser = TorBrowser(archives_dir=archive_root)
    browser.ensure_launched = AsyncMock()

    with pytest.raises(ValueError):
        run(browser.archive_page("linked"))

    assert list(outside.iterdir()) == []
    browser.ensure_launched.assert_not_awaited()


def test_query_elements_passes_selector_as_evaluate_argument(tmp_path):
    calls = []

    class Page:
        async def evaluate(self, *args):
            calls.append(args)
            return []

    selector = "a[href=\"x'); globalThis.pwned = true; ('\"]"
    browser = TorBrowser(archives_dir=tmp_path)
    browser._launched = True
    browser._page = Page()

    assert run(browser.query_elements(selector)) == []
    assert len(calls) == 1
    script, argument = calls[0]
    assert argument == selector
    assert selector not in script
    assert "querySelectorAll" in script


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://2130706433/admin",
        "file:///etc/passwd",
    ],
)
def test_request_gate_aborts_unsafe_redirects_and_page_initiated_navigation(tmp_path, url):
    class Request:
        def __init__(self):
            self.url = url

        def is_navigation_request(self):
            return True

    class Route:
        def __init__(self):
            self.request = Request()
            self.aborted = None
            self.continued = False

        async def abort(self, reason):
            self.aborted = reason

        async def continue_(self):
            self.continued = True

    route = Route()
    browser = TorBrowser(archives_dir=tmp_path)

    run(browser._guard_navigation_request(route))

    assert route.aborted == "blockedbyclient"
    assert route.continued is False


def test_request_gate_allows_public_navigation_and_non_navigation_requests(tmp_path):
    class Request:
        def __init__(self, url, navigation):
            self.url = url
            self._navigation = navigation

        def is_navigation_request(self):
            return self._navigation

    class Route:
        def __init__(self, request):
            self.request = request
            self.continued = False

        async def abort(self, reason):
            raise AssertionError(f"unexpected abort: {reason}")

        async def continue_(self):
            self.continued = True

    browser = TorBrowser(archives_dir=tmp_path)
    public = Route(Request("https://example.com/", True))
    asset = Route(Request("data:image/png;base64,AA==", False))

    run(browser._guard_navigation_request(public))
    run(browser._guard_navigation_request(asset))

    assert public.continued is True
    assert asset.continued is True


def test_request_gate_blocks_private_http_subresources(tmp_path):
    class Request:
        url = "http://0x7f000001/internal-api"

        def is_navigation_request(self):
            return False

    route = type(
        "Route",
        (),
        {
            "request": Request(),
            "abort": AsyncMock(),
            "continue_": AsyncMock(),
        },
    )()
    browser = TorBrowser(archives_dir=tmp_path)

    run(browser._guard_navigation_request(route))

    route.abort.assert_awaited_once_with("blockedbyclient")
    route.continue_.assert_not_awaited()


def test_tor_connection_check_uses_and_closes_a_temporary_page(tmp_path):
    class ActivePage:
        async def goto(self, *args, **kwargs):
            raise AssertionError("the active browsing page must not be navigated")

    class TemporaryPage:
        def __init__(self):
            self.closed = False

        async def goto(self, url, **kwargs):
            assert url == "https://check.torproject.org/api/ip"

        async def inner_text(self, selector):
            assert selector == "body"
            return json.dumps({"IsTor": True, "IP": "203.0.113.7"})

        async def close(self):
            self.closed = True

    class Context:
        def __init__(self, page):
            self.page = page

        async def new_page(self):
            return self.page

    active = ActivePage()
    temporary = TemporaryPage()
    browser = TorBrowser(archives_dir=tmp_path)
    browser._launched = True
    browser._page = active
    browser._context = Context(temporary)

    result = run(browser.check_tor_connection())

    assert result == {"connected": True, "ip": "203.0.113.7"}
    assert temporary.closed is True
    assert browser.page is active


class _LifecycleResource:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


class _FakeContext(_LifecycleResource):
    def __init__(self, *, fail_new_page=False):
        super().__init__()
        self.fail_new_page = fail_new_page
        self.init_script_calls = 0
        self.new_page_calls = 0
        self.route_calls = []
        self.page = object()

    async def route(self, pattern, handler):
        self.route_calls.append((pattern, handler))

    async def add_init_script(self, script):
        self.init_script_calls += 1

    async def new_page(self):
        self.new_page_calls += 1
        if self.fail_new_page:
            raise RuntimeError("page creation failed")
        return self.page


class _FakeBrowser(_LifecycleResource):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.new_context_calls = 0

    async def new_context(self, **kwargs):
        self.new_context_calls += 1
        return self.context


class _FakeFirefox:
    def __init__(self, browser):
        self.browser = browser
        self.launch_calls = 0

    async def launch(self, **kwargs):
        self.launch_calls += 1
        await asyncio.sleep(0)
        return self.browser


class _FakePlaywright:
    def __init__(self, firefox):
        self.firefox = firefox
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1


class _FakePlaywrightStarter:
    def __init__(self, playwright):
        self.playwright = playwright
        self.start_calls = 0

    async def start(self):
        self.start_calls += 1
        await asyncio.sleep(0)
        return self.playwright


def _fake_browser_stack(*, fail_new_page=False):
    context = _FakeContext(fail_new_page=fail_new_page)
    browser = _FakeBrowser(context)
    firefox = _FakeFirefox(browser)
    playwright = _FakePlaywright(firefox)
    starter = _FakePlaywrightStarter(playwright)
    return starter, playwright, firefox, browser, context


def test_concurrent_launch_initializes_exactly_one_browser(monkeypatch, tmp_path):
    starter, playwright, firefox, launched_browser, context = _fake_browser_stack()
    monkeypatch.setattr(browser_module, "async_playwright", lambda: starter)
    browser = TorBrowser(archives_dir=tmp_path)

    async def launch_concurrently():
        await asyncio.gather(*(browser.ensure_launched() for _ in range(20)))

    run(launch_concurrently())

    assert starter.start_calls == 1
    assert firefox.launch_calls == 1
    assert launched_browser.new_context_calls == 1
    assert context.new_page_calls == 1
    assert len(context.route_calls) == 1
    assert browser.page is context.page


def test_partial_launch_failure_cleans_up_every_created_resource(monkeypatch, tmp_path):
    starter, playwright, firefox, launched_browser, context = _fake_browser_stack(
        fail_new_page=True
    )
    monkeypatch.setattr(browser_module, "async_playwright", lambda: starter)
    browser = TorBrowser(archives_dir=tmp_path)

    with pytest.raises(RuntimeError, match="page creation failed"):
        run(browser.launch())

    assert context.close_calls == 1
    assert launched_browser.close_calls == 1
    assert playwright.stop_calls == 1
    assert browser._page is None
    assert browser._context is None
    assert browser._browser is None
    assert browser._playwright is None
    assert browser._launched is False


def test_close_is_idempotent_and_resets_all_references(tmp_path):
    context = _LifecycleResource()
    launched_browser = _LifecycleResource()
    playwright = _FakePlaywright(firefox=None)
    browser = TorBrowser(archives_dir=tmp_path)
    browser._context = context
    browser._browser = launched_browser
    browser._playwright = playwright
    browser._page = object()
    browser._launched = True

    run(browser.close())
    run(browser.close())

    assert context.close_calls == 1
    assert launched_browser.close_calls == 1
    assert playwright.stop_calls == 1
    assert browser._page is None
    assert browser._context is None
    assert browser._browser is None
    assert browser._playwright is None
    assert browser._launched is False
