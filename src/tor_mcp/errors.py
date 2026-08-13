"""Error classification and structured error responses for OpenTor MCP.

Defines an error taxonomy (transient / permanent / configuration) and
provides helpers to classify exceptions into structured error dicts
with actionable recovery guidance.
"""

import re
from typing import Any

# ── Error categories ───────────────────────────────────────────────

TRANSIENT = "transient"
PERMANENT = "permanent"
CONFIGURATION = "configuration"

# ── Sensitive field detection ──────────────────────────────────────

SENSITIVE_FIELD_RE = re.compile(
    r"(password|passwd|secret|token|api.?key|auth|credential)",
    re.IGNORECASE,
)


def structured_error(
    category: str,
    message: str,
    suggestion: str,
    *,
    retryable: bool,
) -> dict[str, Any]:
    """Build a structured error dict with classification and recovery guidance."""
    return {
        "category": category,
        "message": message,
        "suggestion": suggestion,
        "retryable": retryable,
    }


def classify_error(exc: Exception) -> dict[str, Any]:
    """Classify an exception into a structured error with recovery guidance.

    Unknown exception types default to transient (retryable) as a safe
    default — it is always safer to suggest a retry than to declare a
    permanent failure when the cause is unclear.
    """
    message = str(exc)
    lower = message.lower()
    exc_type = type(exc).__name__.lower()

    # ── Timeout errors ─────────────────────────────────────────
    if isinstance(exc, TimeoutError) or "timeout" in exc_type:
        return structured_error(
            TRANSIENT,
            message,
            "The page timed out. Try increasing the timeout or "
            "retry — the site may be temporarily unreachable.",
            retryable=True,
        )

    # ── Configuration errors (dependencies before control port) ─
    if _is_dependency_error(exc, lower):
        return structured_error(
            CONFIGURATION,
            message,
            "A required dependency is missing. Install it and "
            "restart the server.",
            retryable=False,
        )

    if _is_control_port_error(lower):
        return structured_error(
            CONFIGURATION,
            message,
            "The Tor control port is unreachable. Verify that Tor "
            "is running and the control port is configured correctly.",
            retryable=False,
        )

    # ── Connection / network errors ────────────────────────────
    if isinstance(exc, ConnectionError) or _is_network_error(lower):
        return structured_error(
            TRANSIENT,
            message,
            "A network error occurred. The site may be temporarily "
            "unreachable — retry or rotate the Tor circuit.",
            retryable=True,
        )

    # ── Permanent value / input errors ─────────────────────────
    if isinstance(exc, ValueError):
        if "element not found" in lower:
            return structured_error(
                PERMANENT,
                message,
                "The CSS selector did not match any element. Verify "
                "the selector against the current page structure.",
                retryable=False,
            )
        if _is_url_error(lower):
            return structured_error(
                PERMANENT,
                message,
                "Check the URL format. Only absolute http:// and "
                "https:// URLs to public or .onion hosts are allowed.",
                retryable=False,
            )
        return structured_error(
            PERMANENT,
            message,
            "The provided input is invalid. Check the parameter "
            "values and try again with corrected input.",
            retryable=False,
        )

    if isinstance(exc, PermissionError):
        return structured_error(
            PERMANENT,
            message,
            "This operation is not permitted with the current "
            "configuration.",
            retryable=False,
        )

    # ── Execution context / page destruction ───────────────────
    if "execution context" in lower or "target closed" in lower:
        return structured_error(
            TRANSIENT,
            message,
            "The page was destroyed during the operation. Navigate "
            "to the page again and retry.",
            retryable=True,
        )

    # ── Safe default: treat unknown as transient ───────────────
    return structured_error(
        TRANSIENT,
        message,
        "An unexpected error occurred. Retry the operation or try "
        "a different approach.",
        retryable=True,
    )


def redact_sensitive_values(
    message: str,
    *,
    context: dict[str, str] | None = None,
) -> str:
    """Remove sensitive field values from an error message.

    When *context* maps field identifiers (CSS selectors, names, types)
    to the values that were submitted, any value whose field identifier
    matches a sensitive-field pattern is scrubbed from *message*.
    """
    if not context:
        return message
    for field_id, value in context.items():
        if value and SENSITIVE_FIELD_RE.search(field_id):
            message = message.replace(value, "[REDACTED]")
    return message


# ── Private helpers ────────────────────────────────────────────────


def _is_network_error(lower: str) -> bool:
    """Return True when the lowered message indicates a network failure."""
    network_terms = (
        "connection",
        "network",
        "err_connection",
        "err_timed_out",
        "err_name_not_resolved",
        "socket",
        "refused",
        "reset",
        "circuit",
    )
    return any(term in lower for term in network_terms)


def _is_control_port_error(lower: str) -> bool:
    """Return True when the lowered message points to a control-port issue."""
    control_terms = (
        "control port",
        "control-port",
        "authenticate",
        "stem",
    )
    return any(term in lower for term in control_terms)


def _is_url_error(lower: str) -> bool:
    """Return True when the lowered message relates to a URL policy error."""
    url_terms = ("url", "scheme", "hostname", "credential")
    return any(term in lower for term in url_terms)


def _is_dependency_error(exc: Exception, lower: str) -> bool:
    """Return True when the exception indicates a missing dependency."""
    return (
        isinstance(exc, (ImportError, ModuleNotFoundError))
        or "no module" in lower
    )
