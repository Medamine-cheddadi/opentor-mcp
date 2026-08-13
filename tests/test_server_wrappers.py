"""Thin MCP wrapper and singleton coverage using deterministic collaborators."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import tor_mcp.server as server


def run(coro):
    return asyncio.run(coro)


class Browser:
    def __init__(self):
        self.events = []
        self.cookies = [{"name": "sid", "value": "value"}]

    async def navigate(self, url, timeout, **kwargs):
        self.events.append(("navigate", url, timeout))
        return {"url": url, "status": 200}

    async def go_back(self):
        return {"url": "back"}

    async def go_forward(self):
        return {"url": "forward"}

    async def refresh(self):
        return {"url": "refresh"}

    async def get_page_info(self):
        return {"url": "https://example.com", "title": "Title"}

    async def click(self, selector, timeout):
        return f"click:{selector}:{timeout}"

    async def type_text(self, selector, text):
        return f"type:{selector}:{text}"

    async def press_key(self, key):
        return f"key:{key}"

    async def scroll(self, direction, amount):
        return f"scroll:{direction}:{amount}"

    async def get_html(self):
        return "<html><body>plain fallback content</body></html>"

    async def get_content(self):
        return "plain fallback content"

    async def get_cookies(self):
        return self.cookies

    async def current_url(self):
        return "https://example.com/current"

    async def set_cookies(self, cookies):
        self.events.append(("set_cookies", cookies))

    async def new_identity(self):
        return "new identity"

    async def check_tor_connection(self):
        return {"connected": True, "ip": "198.51.100.1"}

    async def archive_page(self, name):
        return f"archive:{name}"

    async def close(self):
        self.events.append(("close",))


class Sessions:
    def __init__(self, loaded=None, listed=None):
        self.loaded = loaded
        self.listed = [] if listed is None else listed
        self.events = []

    async def save(self, name, cookies, url):
        self.events.append(("save", name, cookies, url))
        return "saved"

    async def load(self, name):
        self.events.append(("load", name))
        return self.loaded

    async def list_sessions(self):
        return self.listed

    async def delete(self, name):
        return f"deleted:{name}"


def test_singleton_getters_construct_once(monkeypatch, tmp_path):
    created = {"browser": [], "captcha": [], "sessions": []}

    class BrowserConstructor:
        def __init__(self, **kwargs):
            created["browser"].append(kwargs)

    class CaptchaConstructor:
        def __init__(self):
            created["captcha"].append(True)

    class SessionConstructor:
        def __init__(self, **kwargs):
            created["sessions"].append(kwargs)

    monkeypatch.setattr(server, "_browser", None)
    monkeypatch.setattr(server, "_captcha", None)
    monkeypatch.setattr(server, "_sessions", None)
    monkeypatch.setattr(server, "TorBrowser", BrowserConstructor)
    monkeypatch.setattr(server, "CaptchaSolver", CaptchaConstructor)
    monkeypatch.setattr(server, "SessionStore", SessionConstructor)
    monkeypatch.setattr(server, "ARCHIVES_DIR", tmp_path / "archives")
    monkeypatch.setattr(server, "SESSIONS_DIR", tmp_path / "sessions")

    assert server.get_browser() is server.get_browser()
    assert server.get_captcha() is server.get_captcha()
    assert server.get_sessions() is server.get_sessions()
    assert len(created["browser"]) == len(created["captcha"]) == len(created["sessions"]) == 1
    assert created["browser"][0]["archives_dir"] == tmp_path / "archives"


def test_server_lifespan_closes_existing_browser(monkeypatch):
    browser = Browser()
    monkeypatch.setattr(server, "_browser", browser)

    async def enter_and_exit():
        async with server.server_lifespan(SimpleNamespace()) as state:
            assert state == {}

    run(enter_and_exit())
    assert browser.events == [("close",)]


def test_server_lifespan_tolerates_no_browser(monkeypatch):
    monkeypatch.setattr(server, "_browser", None)

    async def enter_and_exit():
        async with server.server_lifespan(SimpleNamespace()):
            pass

    run(enter_and_exit())


def test_navigation_and_interaction_wrappers(monkeypatch):
    browser = Browser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)

    assert json.loads(run(server.tor_navigate("https://example.com", 42)))["data"]["status"] == 200
    assert json.loads(run(server.tor_back()))["data"]["url"] == "back"
    assert json.loads(run(server.tor_forward()))["data"]["url"] == "forward"
    assert json.loads(run(server.tor_refresh()))["data"]["url"] == "refresh"
    assert json.loads(run(server.tor_get_page_info()))["data"]["title"] == "Title"
    assert run(server.tor_click("#go", 9)) == "click:#go:9"
    assert run(server.tor_type("#input", "text")) == "type:#input:text"
    assert run(server.tor_press_key("Tab")) == "key:Tab"
    assert run(server.tor_scroll("up", 22)) == "scroll:up:22"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: server._clamp_limit(0), "at least 1"),
        (lambda: server._clamp_offset(-1), "negative"),
        (lambda: server._truncate("value", 0), "at least 1"),
        (lambda: server._paginate([], -1, 1), "negative"),
        (lambda: server._paginate([], 0, 0), "at least 1"),
    ],
)
def test_pagination_and_truncation_reject_invalid_values(call, message):
    with pytest.raises(ValueError, match=message):
        call()


def test_thread_tool_returns_untrusted_plain_text_fallback(monkeypatch):
    browser = Browser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "extract_forum_threads", lambda html: [])

    result = run(server.tor_extract_threads())

    assert result.startswith("[UNTRUSTED WEB CONTENT")
    assert "UNTRUSTED WEB CONTENT" in result
    assert "plain fallback content" in result


def test_session_wrappers_cover_save_load_list_delete(monkeypatch):
    browser = Browser()
    sessions = Sessions(
        loaded={
            "cookies": browser.cookies,
            "cookie_count": 1,
            "age_hours": 1.5,
            "url": "https://example.com/original",
        },
        listed=[{"name": "forum", "cookie_count": 1}],
    )
    monkeypatch.setattr(server, "get_browser", lambda: browser)
    monkeypatch.setattr(server, "get_sessions", lambda: sessions)

    assert run(server.tor_save_session("forum")) == "saved"
    loaded = run(server.tor_load_session("forum"))
    assert "1 cookies restored" in loaded
    assert ("set_cookies", browser.cookies) in browser.events
    assert json.loads(run(server.tor_list_sessions()))["data"][0]["name"] == "forum"
    assert run(server.tor_delete_session("forum")) == "deleted:forum"


def test_session_wrappers_cover_missing_and_empty_states(monkeypatch):
    sessions = Sessions(loaded=None, listed=[])
    monkeypatch.setattr(server, "get_browser", Browser)
    monkeypatch.setattr(server, "get_sessions", lambda: sessions)

    assert run(server.tor_load_session("missing")) == "Session 'missing' not found."
    assert run(server.tor_list_sessions()) == "No saved sessions."


def test_control_and_archive_wrappers(monkeypatch):
    browser = Browser()
    monkeypatch.setattr(server, "get_browser", lambda: browser)

    assert run(server.tor_new_identity()) == "new identity"
    assert json.loads(run(server.tor_check_connection()))["data"]["connected"] is True
    assert run(server.tor_archive_page("copy")) == "archive:copy"


def test_main_runs_stdio_transport(monkeypatch):
    run_mock = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: run_mock.append(kwargs))

    server.main()

    assert run_mock == [{"transport": "stdio"}]
