"""Deterministic coverage for the browser facade without Playwright or Tor."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tor_mcp.browser import TorBrowser


def run(coro):
    return asyncio.run(coro)


class FakeElement:
    async def screenshot(self):
        return b"element-png"


class FakeKeyboard:
    def __init__(self, events):
        self.events = events

    async def press(self, key):
        self.events.append(("press", key))


class FakePage:
    def __init__(self):
        self.url = "https://example.com/current"
        self.events = []
        self.keyboard = FakeKeyboard(self.events)
        self.response = type("Response", (), {"status": 204})()
        self.element = FakeElement()
        self.wait_error = None

    async def goto(self, url, **kwargs):
        self.events.append(("goto", url, kwargs))
        if url.endswith("/error"):
            raise RuntimeError("navigation failed")
        self.url = url
        return self.response

    async def wait_for_timeout(self, milliseconds):
        self.events.append(("timeout", milliseconds))

    async def title(self):
        return "Example"

    async def go_back(self):
        self.events.append(("back",))

    async def go_forward(self):
        self.events.append(("forward",))

    async def reload(self):
        self.events.append(("reload",))

    async def click(self, selector, **kwargs):
        self.events.append(("click", selector, kwargs))

    async def fill(self, selector, value):
        self.events.append(("fill", selector, value))

    async def type(self, selector, value, **kwargs):
        self.events.append(("type", selector, value, kwargs))

    async def select_option(self, selector, value):
        self.events.append(("select", selector, value))

    async def evaluate(self, script, *args):
        self.events.append(("evaluate", script, args))
        if script == "2 + 2":
            return 4
        if "links_count" in script:
            return {"url": self.url, "title": "Example", "links_count": 1}
        if "querySelectorAll('a[href]')" in script:
            return [{"text": "Example", "href": "https://example.com"}]
        if args:
            return [{"index": 0, "tag": "div", "text": "result"}]
        return None

    async def wait_for_selector(self, selector, **kwargs):
        self.events.append(("wait", selector, kwargs))
        if self.wait_error:
            raise self.wait_error

    async def screenshot(self, **kwargs):
        self.events.append(("screenshot", kwargs))
        return b"page-png"

    async def query_selector(self, selector):
        self.events.append(("query", selector))
        return self.element

    async def inner_text(self, selector):
        return "page text"

    async def content(self):
        return "<html><body>page text</body></html>"


class FakeContext:
    def __init__(self):
        self.events = []
        self.cookie_values = [{"name": "sid", "value": "secret"}]

    async def cookies(self):
        return self.cookie_values

    async def add_cookies(self, cookies):
        self.events.append(("add", cookies))

    async def clear_cookies(self):
        self.events.append(("clear",))


@pytest.fixture
def browser(tmp_path):
    instance = TorBrowser(archives_dir=tmp_path / "archives")
    instance._launched = True
    instance._page = FakePage()
    instance._context = FakeContext()
    return instance


def test_page_and_context_properties_require_launch(tmp_path):
    browser = TorBrowser(archives_dir=tmp_path)

    with pytest.raises(RuntimeError, match="Browser not launched"):
        _ = browser.page
    with pytest.raises(RuntimeError, match="Browser not launched"):
        _ = browser.context


def test_navigation_history_and_current_url(browser):
    result = run(browser.navigate("https://example.com/next", timeout=321))
    back = run(browser.go_back())
    forward = run(browser.go_forward())
    refreshed = run(browser.refresh())

    assert result == {
        "url": "https://example.com/next",
        "title": "Example",
        "status": 204,
    }
    assert back["title"] == forward["title"] == refreshed["title"] == "Example"
    assert run(browser.current_url()) == "https://example.com/next"


def test_navigation_handles_no_response_and_page_errors(browser):
    browser.page.response = None
    assert run(browser.navigate("https://example.com/no-response"))["status"] is None

    result = run(browser.navigate("https://example.com/error"))
    error = result["error"]
    assert error["message"] == "navigation failed"
    assert error["category"] == "transient"
    assert error["suggestion"]
    assert error["retryable"] is True
    assert result["status"] is None


def test_interaction_methods_delegate_to_page(browser):
    assert run(browser.click("#submit", timeout=55)) == "Clicked: #submit"
    assert run(browser.type_text("#name", "Alice", delay=3)) == "Typed into: #name"
    assert run(browser.press_key("Enter")) == "Pressed: Enter"
    assert run(browser.select_option("select", "one")) == "Selected one in select"

    assert ("fill", "#name", "") in browser.page.events
    assert ("type", "#name", "Alice", {"delay": 3}) in browser.page.events
    assert ("press", "Enter") in browser.page.events


@pytest.mark.parametrize(
    ("direction", "script_fragment", "args"),
    [
        ("top", "window.scrollTo(0, 0)", ()),
        ("bottom", "document.body.scrollHeight", ()),
        ("up", "-amount", (37,)),
        ("down", "window.scrollBy(0, amount)", (37,)),
    ],
)
def test_scroll_supports_each_direction(browser, direction, script_fragment, args):
    result = run(browser.scroll(direction, 37))
    event = browser.page.events[-1]

    assert result == f"Scrolled {direction} by 37px"
    assert event[0] == "evaluate"
    assert script_fragment in event[1]
    assert event[2] == args


def test_scroll_rejects_unknown_direction(browser):
    with pytest.raises(ValueError, match="Direction"):
        run(browser.scroll("sideways"))


def test_wait_for_reports_success_and_timeout(browser):
    assert run(browser.wait_for("#ready", timeout=9)) is True
    browser.page.wait_error = TimeoutError("not found")
    assert run(browser.wait_for("#missing", timeout=9)) is False


def test_reading_and_screenshot_methods(browser):
    assert run(browser.screenshot(full_page=True)) == b"page-png"
    assert run(browser.screenshot_element("#logo")) == b"element-png"
    assert run(browser.get_content()) == "page text"
    assert run(browser.get_html()).startswith("<html>")
    assert run(browser.get_links())[0]["text"] == "Example"
    assert run(browser.get_page_info())["links_count"] == 1
    assert run(browser.evaluate_js("2 + 2")) == "4"
    assert run(browser.query_elements("div"))[0]["tag"] == "div"

    browser.page.element = None
    with pytest.raises(ValueError, match="Element not found"):
        run(browser.screenshot_element("#missing"))


def test_cookie_methods_delegate_to_context(browser):
    cookies = run(browser.get_cookies())
    run(browser.set_cookies(cookies))
    run(browser.clear_cookies())

    assert cookies == [{"name": "sid", "value": "secret"}]
    assert browser.context.events == [("add", cookies), ("clear",)]


def test_circuit_rotation_and_identity_paths(monkeypatch, browser):
    async def call_directly(function):
        return function()

    monkeypatch.setattr(asyncio, "to_thread", call_directly)
    browser._signal_newnym = lambda: None
    assert "rotated" in run(browser.rotate_circuit()).lower()
    assert "acquired" in run(browser.new_identity()).lower()

    def fail():
        raise RuntimeError("control port unavailable")

    browser._signal_newnym = fail
    assert "failed" in run(browser.rotate_circuit()).lower()
    assert "cookies were cleared" in run(browser.new_identity()).lower()


def test_connection_check_failure_closes_temporary_page(browser):
    temporary_page = AsyncMock()
    temporary_page.goto.side_effect = RuntimeError("offline")
    browser.context.new_page = AsyncMock(return_value=temporary_page)

    result = run(browser.check_tor_connection())

    assert result == {
        "connected": False,
        "ip": None,
        "error": "Tor connection verification failed.",
    }
    temporary_page.close.assert_awaited_once()


def test_archive_page_writes_complete_owner_private_snapshot(browser):
    result = run(browser.archive_page("snapshot"))
    archive = browser.archives_dir / "snapshot"

    assert result == f"Archived to {archive}"
    assert (archive / "page.html").read_text() == "<html><body>page text</body></html>"
    assert (archive / "content.txt").read_text() == "page text"
    assert (archive / "screenshot.png").read_bytes() == b"page-png"
    assert json.loads((archive / "metadata.json").read_text())["title"] == "Example"
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in archive.iterdir())


# ── Wait strategy tests ────────────────────────────────────────────


def test_navigate_default_strategy_uses_networkidle(browser):
    result = run(browser.navigate("https://example.com/page"))

    goto_events = [e for e in browser.page.events if e[0] == "goto"]
    timeout_events = [e for e in browser.page.events if e[0] == "timeout"]

    assert result["status"] == 204
    assert goto_events[0][2]["wait_until"] == "networkidle"
    # networkidle already waits for network to settle — no extra sleep
    assert timeout_events == []


def test_navigate_fast_strategy_uses_domcontentloaded(browser):
    result = run(browser.navigate("https://example.com/page", wait_strategy="fast"))

    goto_events = [e for e in browser.page.events if e[0] == "goto"]
    timeout_events = [e for e in browser.page.events if e[0] == "timeout"]

    assert result["status"] == 204
    assert goto_events[0][2]["wait_until"] == "domcontentloaded"
    assert timeout_events == []


def test_navigate_full_strategy_waits_for_selector(browser):
    result = run(
        browser.navigate(
            "https://example.com/page",
            wait_strategy="full",
            wait_selector="#content",
        )
    )

    goto_events = [e for e in browser.page.events if e[0] == "goto"]
    wait_events = [e for e in browser.page.events if e[0] == "wait"]

    assert result["status"] == 204
    assert "error" not in result
    assert goto_events[0][2]["wait_until"] == "networkidle"
    assert wait_events[0][1] == "#content"


def test_navigate_full_strategy_selector_timeout_returns_error(browser):
    browser.page.wait_error = TimeoutError("selector never appeared")

    result = run(
        browser.navigate(
            "https://example.com/page",
            wait_strategy="full",
            wait_selector="#missing",
        )
    )

    assert result["error"]["category"] == "transient"
    assert result["error"]["retryable"] is True
    assert "#missing" in result["error"]["message"]
    # Navigation itself succeeded — we still have a status and URL
    assert result["status"] == 204
    assert result["url"] == "https://example.com/page"


def test_compatibility_mode_relaxes_stealth_prefs(tmp_path):
    browser = TorBrowser(archives_dir=tmp_path / "archives", compatibility_mode=True)
    assert browser.compatibility_mode is True

    browser_default = TorBrowser(archives_dir=tmp_path / "archives2")
    assert browser_default.compatibility_mode is False


# ── Self-healing retry tests ──────────────────────────────────────


def test_navigate_succeeds_first_try_no_retry(monkeypatch, browser):
    """Happy path: success on first attempt — no rotation, no retry."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    rotation_calls = []
    browser.rotate_circuit = AsyncMock(side_effect=lambda: rotation_calls.append(1) or "rotated")

    result = run(browser.navigate("https://example.com/ok", max_retries=2, retry_backoff=0.01))

    assert "error" not in result
    assert result["url"] == "https://example.com/ok"
    assert rotation_calls == []
    asyncio.sleep.assert_not_awaited()


def test_navigate_retries_on_transient_then_succeeds(monkeypatch, browser):
    """Navigation fails with timeout, rotation succeeds, retry succeeds."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    call_count = 0
    original_goto = browser.page.goto

    async def flaky_goto(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("page timed out")
        return await original_goto(url, **kwargs)

    browser.page.goto = flaky_goto
    browser.rotate_circuit = AsyncMock(return_value="Tor circuit rotated. Cookies preserved.")

    result = run(browser.navigate("https://example.com/ok", max_retries=2, retry_backoff=0.01))

    assert "error" not in result
    assert result["url"] == "https://example.com/ok"
    browser.rotate_circuit.assert_awaited_once()
    asyncio.sleep.assert_awaited_once()


def test_navigate_rotation_fails_retry_succeeds_with_warning(monkeypatch, browser):
    """Rotation fails (control port down), retry succeeds without rotation."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    call_count = 0
    original_goto = browser.page.goto

    async def flaky_goto(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("page timed out")
        return await original_goto(url, **kwargs)

    browser.page.goto = flaky_goto
    rotation_msg = (
        "Tor circuit rotation failed."
        " Check the authenticated control-port configuration."
    )
    browser.rotate_circuit = AsyncMock(return_value=rotation_msg)

    result = run(browser.navigate("https://example.com/ok", max_retries=2, retry_backoff=0.01))

    assert "error" not in result
    assert result["url"] == "https://example.com/ok"
    assert "rotation_warning" in result
    assert "failed" in result["rotation_warning"].lower()


def test_navigate_all_retries_exhausted(monkeypatch, browser):
    """All retries exhausted — permanent error with new_identity suggestion."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def always_fail(url, **kwargs):
        raise TimeoutError("page timed out")

    browser.page.goto = always_fail
    browser.rotate_circuit = AsyncMock(return_value="Tor circuit rotated. Cookies preserved.")

    result = run(browser.navigate("https://example.com/stuck", max_retries=2, retry_backoff=0.01))

    assert result["error"]["category"] == "transient"
    assert "tor_new_identity" in result["error"]["suggestion"]
    assert "exhausted" in result["error"]["suggestion"].lower()
    assert result["url"] == "https://example.com/stuck"
    assert result["title"] is None
    assert result["status"] is None


def test_navigate_rotation_succeeds_but_retry_still_fails(monkeypatch, browser):
    """Rotation succeeds but retry still fails — exhausts budget."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def always_fail(url, **kwargs):
        raise TimeoutError("page timed out")

    browser.page.goto = always_fail
    browser.rotate_circuit = AsyncMock(return_value="Tor circuit rotated. Cookies preserved.")

    result = run(browser.navigate("https://example.com/down", max_retries=1, retry_backoff=0.01))

    assert "error" in result
    assert "tor_new_identity" in result["error"]["suggestion"]
    assert browser.rotate_circuit.await_count == 1


def test_navigate_max_retries_zero_no_retry(monkeypatch, browser):
    """Max retries set to 0 — no retry, immediate error return."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    async def always_fail(url, **kwargs):
        raise TimeoutError("page timed out")

    browser.page.goto = always_fail
    browser.rotate_circuit = AsyncMock(return_value="rotated")

    result = run(browser.navigate("https://example.com/fail", max_retries=0, retry_backoff=0.01))

    assert "error" in result
    assert result["error"]["category"] == "transient"
    browser.rotate_circuit.assert_not_awaited()
    asyncio.sleep.assert_not_awaited()


def test_navigate_permanent_error_not_retried(monkeypatch, browser):
    """Permanent errors are not retried regardless of retry budget."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    browser.rotate_circuit = AsyncMock(return_value="rotated")

    async def permanent_fail(url, **kwargs):
        raise ValueError("Element not found: #missing")

    browser.page.goto = permanent_fail

    result = run(browser.navigate("https://example.com/page", max_retries=2, retry_backoff=0.01))

    assert result["error"]["category"] == "permanent"
    assert result["error"]["retryable"] is False
    browser.rotate_circuit.assert_not_awaited()


def test_navigate_retry_uses_structured_errors(monkeypatch, browser):
    """Retry loop uses structured errors from Unit 1 to decide retry eligibility."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    call_count = 0
    original_goto = browser.page.goto

    async def fail_once(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("connection reset by peer")
        return await original_goto(url, **kwargs)

    browser.page.goto = fail_once
    browser.rotate_circuit = AsyncMock(return_value="Tor circuit rotated. Cookies preserved.")

    result = run(browser.navigate("https://example.com/ok", max_retries=2, retry_backoff=0.01))

    assert "error" not in result
    assert result["url"] == "https://example.com/ok"
    assert call_count == 2


def test_navigate_retry_backoff_is_exponential(monkeypatch, browser):
    """Backoff delays double on each retry attempt."""
    sleep_delays = []

    async def capture_sleep(delay):
        sleep_delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", capture_sleep)

    async def always_fail(url, **kwargs):
        raise TimeoutError("timed out")

    browser.page.goto = always_fail
    browser.rotate_circuit = AsyncMock(return_value="rotated")

    run(browser.navigate("https://example.com/slow", max_retries=3, retry_backoff=2.0))

    assert sleep_delays == [2.0, 4.0, 8.0]
