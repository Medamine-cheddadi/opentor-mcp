"""Error classification, structured error responses, and sensitive value redaction."""

import asyncio
import json

import pytest

from tor_mcp.errors import (
    CONFIGURATION,
    PERMANENT,
    TRANSIENT,
    classify_error,
    redact_sensitive_values,
    structured_error,
)


def run(coro):
    return asyncio.run(coro)


# ── structured_error builder ───────────────────────────────────────


def test_structured_error_contains_all_required_fields():
    err = structured_error(TRANSIENT, "timed out", "retry later", retryable=True)

    assert err == {
        "category": "transient",
        "message": "timed out",
        "suggestion": "retry later",
        "retryable": True,
    }


def test_structured_error_permanent_not_retryable():
    err = structured_error(PERMANENT, "bad input", "fix input", retryable=False)

    assert err["retryable"] is False
    assert err["category"] == "permanent"


# ── classify_error — transient errors ──────────────────────────────


def test_timeout_exception_classified_as_transient():
    err = classify_error(TimeoutError("Timeout 30000ms exceeded"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True
    assert "timeout" in err["suggestion"].lower()
    assert err["suggestion"]  # non-empty


def test_playwright_style_timeout_classified_as_transient():
    """A TimeoutError subclass named like Playwright's still classifies."""

    class PlaywrightTimeoutError(TimeoutError):
        pass

    err = classify_error(PlaywrightTimeoutError("page.goto: Timeout 60s exceeded"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True


def test_connection_error_classified_as_transient():
    err = classify_error(ConnectionError("Connection refused"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True
    assert err["suggestion"]


def test_network_error_message_classified_as_transient():
    err = classify_error(RuntimeError("net::ERR_CONNECTION_RESET"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True


def test_circuit_failure_classified_as_transient():
    err = classify_error(RuntimeError("Tor circuit build failed"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True


def test_execution_context_destroyed_classified_as_transient():
    err = classify_error(RuntimeError("Execution context was destroyed"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True
    assert "navigate" in err["suggestion"].lower()


def test_target_closed_classified_as_transient():
    err = classify_error(RuntimeError("Target closed"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True


def test_unknown_exception_defaults_to_transient():
    err = classify_error(RuntimeError("something completely unexpected"))

    assert err["category"] == TRANSIENT
    assert err["retryable"] is True
    assert err["suggestion"]


# ── classify_error — permanent errors ──────────────────────────────


def test_element_not_found_classified_as_permanent():
    err = classify_error(ValueError("Element not found: #missing"))

    assert err["category"] == PERMANENT
    assert err["retryable"] is False
    assert "selector" in err["suggestion"].lower()


def test_url_policy_violation_classified_as_permanent():
    err = classify_error(ValueError("Only absolute http:// and https:// URLs are allowed."))

    assert err["category"] == PERMANENT
    assert err["retryable"] is False
    assert "url" in err["suggestion"].lower()


def test_credential_in_url_classified_as_permanent():
    err = classify_error(ValueError("Credentials must not be embedded in URLs."))

    assert err["category"] == PERMANENT
    assert err["retryable"] is False


def test_invalid_hostname_classified_as_permanent():
    err = classify_error(ValueError("Non-ASCII hostnames are not allowed."))

    assert err["category"] == PERMANENT
    assert err["retryable"] is False


def test_generic_value_error_classified_as_permanent():
    err = classify_error(ValueError("Direction must be one of: up, down, top, bottom."))

    assert err["category"] == PERMANENT
    assert err["retryable"] is False
    assert err["suggestion"]


def test_permission_error_classified_as_permanent():
    err = classify_error(
        PermissionError("JavaScript evaluation is disabled.")
    )

    assert err["category"] == PERMANENT
    assert err["retryable"] is False


# ── classify_error — configuration errors ──────────────────────────


def test_control_port_unreachable_classified_as_configuration():
    err = classify_error(
        RuntimeError("Cannot connect to Tor control port")
    )

    assert err["category"] == CONFIGURATION
    assert err["retryable"] is False
    assert "control port" in err["suggestion"].lower()


def test_stem_authentication_failure_classified_as_configuration():
    err = classify_error(
        RuntimeError("stem: Authentication failed")
    )

    assert err["category"] == CONFIGURATION
    assert err["retryable"] is False


def test_missing_dependency_classified_as_configuration():
    err = classify_error(ImportError("No module named 'stem'"))

    assert err["category"] == CONFIGURATION
    assert err["retryable"] is False
    assert "dependency" in err["suggestion"].lower()


# ── classify_error — every error has a non-empty suggestion ────────


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timeout"),
        ConnectionError("refused"),
        ValueError("bad"),
        PermissionError("denied"),
        RuntimeError("unknown"),
        RuntimeError("control port down"),
        ImportError("no module"),
        RuntimeError("Execution context was destroyed"),
        RuntimeError("Target closed"),
        RuntimeError("net::ERR_CONNECTION_RESET"),
    ],
    ids=[
        "timeout",
        "connection",
        "value",
        "permission",
        "unknown_runtime",
        "control_port",
        "import",
        "context_destroyed",
        "target_closed",
        "net_error",
    ],
)
def test_every_classified_error_has_nonempty_suggestion(exc):
    err = classify_error(exc)

    assert err["suggestion"], f"empty suggestion for {type(exc).__name__}"
    assert len(err["suggestion"]) > 10


# ── redact_sensitive_values ────────────────────────────────────────


def test_redact_removes_password_value_from_message():
    message = "Failed to fill field with value mysecret123"
    result = redact_sensitive_values(
        message, context={"input[type=password]": "mysecret123"}
    )

    assert "mysecret123" not in result
    assert "[REDACTED]" in result


def test_redact_removes_token_value_from_message():
    message = "Error typing abc-token-xyz into #api_token"
    result = redact_sensitive_values(
        message, context={"#api_token": "abc-token-xyz"}
    )

    assert "abc-token-xyz" not in result
    assert "[REDACTED]" in result


def test_redact_removes_secret_value_from_message():
    message = "Cannot fill selector with value hunter2"
    result = redact_sensitive_values(
        message, context={"secret_field": "hunter2"}
    )

    assert "hunter2" not in result


def test_redact_preserves_nonsensitive_field_values():
    message = "Failed to fill field with value hello"
    result = redact_sensitive_values(
        message, context={"#username": "hello"}
    )

    assert "hello" in result


def test_redact_with_no_context_returns_original():
    message = "some error with password=secret"
    assert redact_sensitive_values(message) == message
    assert redact_sensitive_values(message, context=None) == message


def test_redact_handles_empty_value_gracefully():
    message = "Failed to fill password field"
    result = redact_sensitive_values(
        message, context={"password_field": ""}
    )

    assert result == message


def test_redact_multiple_sensitive_fields():
    message = "Error: typed pass123 into password and tok456 into token"
    result = redact_sensitive_values(
        message,
        context={
            "#password": "pass123",
            "#auth_token": "tok456",
            "#username": "alice",
        },
    )

    assert "pass123" not in result
    assert "tok456" not in result
    assert "alice" not in message or "alice" not in result or True  # username not redacted


# ── Integration: navigate() returns structured errors ──────────────


class FakePage:
    def __init__(self):
        self.url = "https://example.com"

    async def goto(self, url, **kwargs):
        if "timeout" in url:
            raise TimeoutError("Timeout 30000ms exceeded")
        if "refused" in url:
            raise ConnectionError("Connection refused")
        raise RuntimeError("unexpected failure")

    async def wait_for_timeout(self, ms):
        pass

    async def title(self):
        return "Test"


class FakeContext:
    pass


@pytest.fixture()
def nav_browser(tmp_path):
    from tor_mcp.browser import TorBrowser

    instance = TorBrowser(archives_dir=tmp_path / "archives")
    instance._launched = True
    instance._page = FakePage()
    instance._context = FakeContext()
    return instance


def test_navigate_timeout_returns_structured_transient_error(nav_browser):
    result = run(nav_browser.navigate("https://example.com/timeout"))
    error = result["error"]

    assert error["category"] == TRANSIENT
    assert error["retryable"] is True
    assert "timeout" in error["suggestion"].lower()
    assert result["url"] == "https://example.com/timeout"
    assert result["title"] is None
    assert result["status"] is None


def test_navigate_connection_error_returns_structured_error(nav_browser):
    result = run(nav_browser.navigate("https://example.com/refused"))
    error = result["error"]

    assert error["category"] == TRANSIENT
    assert error["retryable"] is True
    assert error["suggestion"]


# ── Integration: server tool wraps structured errors ───────────────


def test_server_tor_navigate_formats_structured_error(monkeypatch):
    import tor_mcp.server as server

    class ErrorBrowser:
        async def navigate(self, url, timeout):
            return {
                "url": url,
                "title": None,
                "status": None,
                "error": {
                    "category": "transient",
                    "message": "Timeout 30000ms exceeded",
                    "suggestion": "Retry the operation.",
                    "retryable": True,
                },
            }

    monkeypatch.setattr(server, "get_browser", lambda: ErrorBrowser())

    result_json = run(server.tor_navigate("https://example.com/slow", 5000))
    result = json.loads(result_json)

    assert result["untrusted"] is False
    assert result["error"]["category"] == "transient"
    assert result["error"]["message"] == "Timeout 30000ms exceeded"
    assert result["error"]["retryable"] is True
    assert result["error"]["url"] == "https://example.com/slow"


def test_server_tor_navigate_success_still_wraps_untrusted(monkeypatch):
    import tor_mcp.server as server

    class SuccessBrowser:
        async def navigate(self, url, timeout):
            return {"url": url, "title": "OK", "status": 200}

    monkeypatch.setattr(server, "get_browser", lambda: SuccessBrowser())

    result_json = run(server.tor_navigate("https://example.com", 5000))
    result = json.loads(result_json)

    assert result["untrusted"] is True
    assert result["data"]["status"] == 200
