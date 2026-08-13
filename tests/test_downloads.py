"""Deterministic tests for the file download feature."""

import asyncio

import pytest

from tor_mcp.browser import TorBrowser


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, *, status=200, content_type="application/pdf", body=b"%PDF-fake"):
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = body

    async def body(self):
        return self._body


class FakePage:
    def __init__(self, response=None):
        self.url = "https://example.onion/current"
        self._response = response or FakeResponse()
        self.closed = False

    async def goto(self, url, **kwargs):
        self.url = url
        return self._response

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, fake_page_for_download=None):
        self._download_page = fake_page_for_download

    async def new_page(self):
        return self._download_page or FakePage()


@pytest.fixture
def browser(tmp_path):
    instance = TorBrowser(archives_dir=tmp_path / "archives")
    instance._launched = True
    main_page = FakePage()
    instance._tabs = {"main": main_page}
    instance._active_tab = "main"
    instance._context = FakeContext()
    return instance


@pytest.fixture
def downloads_dir(tmp_path):
    return tmp_path / "downloads"


ALLOWED_TYPES = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/gif",
    "image/webp", "text/plain", "text/csv", "application/json",
})


# ── Happy path ──────────────────────────────────────────────────


def test_download_file_saves_with_safe_name(browser, downloads_dir):
    response = FakeResponse(body=b"%PDF-content", content_type="application/pdf")
    browser._context = FakeContext(FakePage(response))

    result = run(browser.download_file(
        "https://example.onion/docs/report.pdf",
        downloads_dir,
        filename_hint="report.pdf",
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    assert result["filename"] == "report.pdf"
    assert result["size_bytes"] == len(b"%PDF-content")
    assert result["mime_type"] == "application/pdf"

    saved = downloads_dir / "report.pdf"
    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-content"
    assert saved.stat().st_mode & 0o777 == 0o600


def test_download_file_with_valid_mime_type(browser, downloads_dir):
    response = FakeResponse(body=b"\x89PNG", content_type="image/png")
    browser._context = FakeContext(FakePage(response))

    result = run(browser.download_file(
        "https://example.onion/img/photo.png",
        downloads_dir,
        filename_hint="photo.png",
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    assert result["mime_type"] == "image/png"
    assert (downloads_dir / "photo.png").exists()


# ── Filename sanitization ──────────────────────────────────────


def test_path_traversal_filename_sanitized(browser, downloads_dir):
    response = FakeResponse(body=b"data", content_type="text/plain")
    browser._context = FakeContext(FakePage(response))

    result = run(browser.download_file(
        "https://example.onion/file.txt",
        downloads_dir,
        filename_hint="../../etc/passwd",
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    # The path traversal components are stripped; only "passwd" survives.
    assert "/" not in result["filename"]
    assert ".." not in result["filename"]
    saved = downloads_dir / result["filename"]
    assert saved.exists()
    assert saved.read_bytes() == b"data"


def test_no_filename_hint_uses_hash_based_name(browser, downloads_dir):
    response = FakeResponse(body=b"data", content_type="text/plain")
    browser._context = FakeContext(FakePage(response))

    result = run(browser.download_file(
        "https://example.onion/",
        downloads_dir,
        filename_hint=None,
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    assert result["filename"].startswith("download_")
    assert (downloads_dir / result["filename"]).exists()


# ── Size limit ──────────────────────────────────────────────────


def test_file_exceeds_size_limit_rejected(browser, downloads_dir):
    big_body = b"x" * 1000
    response = FakeResponse(body=big_body, content_type="text/plain")
    browser._context = FakeContext(FakePage(response))

    with pytest.raises(ValueError, match="exceeds the limit"):
        run(browser.download_file(
            "https://example.onion/big.txt",
            downloads_dir,
            filename_hint="big.txt",
            max_bytes=500,
            allowed_types=ALLOWED_TYPES,
        ))


# ── URL policy ──────────────────────────────────────────────────


def test_download_url_fails_policy_validation(browser, downloads_dir):
    with pytest.raises(ValueError, match="not allowed"):
        run(browser.download_file(
            "https://127.0.0.1/secret.pdf",
            downloads_dir,
            max_bytes=1_000_000,
            allowed_types=ALLOWED_TYPES,
        ))


def test_download_ftp_scheme_rejected(browser, downloads_dir):
    with pytest.raises(ValueError, match="http"):
        run(browser.download_file(
            "ftp://example.onion/file.txt",
            downloads_dir,
            max_bytes=1_000_000,
            allowed_types=ALLOWED_TYPES,
        ))


# ── MIME filtering ──────────────────────────────────────────────


def test_mime_type_not_in_allowlist_rejected(browser, downloads_dir):
    response = FakeResponse(body=b"<html>", content_type="text/html")
    browser._context = FakeContext(FakePage(response))

    with pytest.raises(ValueError, match="not in the allowed list"):
        run(browser.download_file(
            "https://example.onion/page.html",
            downloads_dir,
            filename_hint="page.html",
            max_bytes=1_000_000,
            allowed_types=ALLOWED_TYPES,
        ))


def test_svg_mime_type_rejected(browser, downloads_dir):
    response = FakeResponse(body=b"<svg>", content_type="image/svg+xml")
    browser._context = FakeContext(FakePage(response))

    with pytest.raises(ValueError, match="not in the allowed list"):
        run(browser.download_file(
            "https://example.onion/icon.svg",
            downloads_dir,
            filename_hint="icon.svg",
            max_bytes=1_000_000,
            allowed_types=ALLOWED_TYPES,
        ))


# ── Directory permissions ──────────────────────────────────────


def test_downloads_directory_created_with_restricted_permissions(browser, downloads_dir):
    response = FakeResponse(body=b"data", content_type="text/plain")
    browser._context = FakeContext(FakePage(response))

    run(browser.download_file(
        "https://example.onion/file.txt",
        downloads_dir,
        filename_hint="file.txt",
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    assert downloads_dir.exists()
    assert downloads_dir.stat().st_mode & 0o777 == 0o700


# ── Temporary page cleanup ─────────────────────────────────────


def test_temporary_page_closed_on_success(browser, downloads_dir):
    response = FakeResponse(body=b"data", content_type="text/plain")
    temp_page = FakePage(response)
    browser._context = FakeContext(temp_page)

    run(browser.download_file(
        "https://example.onion/file.txt",
        downloads_dir,
        filename_hint="file.txt",
        max_bytes=1_000_000,
        allowed_types=ALLOWED_TYPES,
    ))

    assert temp_page.closed is True


def test_temporary_page_closed_on_error(browser, downloads_dir):
    response = FakeResponse(body=b"x" * 1000, content_type="text/plain")
    temp_page = FakePage(response)
    browser._context = FakeContext(temp_page)

    with pytest.raises(ValueError, match="exceeds"):
        run(browser.download_file(
            "https://example.onion/big.txt",
            downloads_dir,
            filename_hint="big.txt",
            max_bytes=500,
            allowed_types=ALLOWED_TYPES,
        ))

    assert temp_page.closed is True


# ── HTTP error status ──────────────────────────────────────────


def test_download_http_error_status_rejected(browser, downloads_dir):
    response = FakeResponse(status=404, body=b"not found", content_type="text/html")
    browser._context = FakeContext(FakePage(response))

    with pytest.raises(ValueError, match="HTTP status 404"):
        run(browser.download_file(
            "https://example.onion/missing.pdf",
            downloads_dir,
            filename_hint="missing.pdf",
            max_bytes=1_000_000,
            allowed_types=ALLOWED_TYPES,
        ))


# ── Empty allowed_types disables MIME filter ───────────────────


def test_empty_allowed_types_accepts_any_mime(browser, downloads_dir):
    response = FakeResponse(body=b"<html>", content_type="text/html")
    browser._context = FakeContext(FakePage(response))

    result = run(browser.download_file(
        "https://example.onion/page.html",
        downloads_dir,
        filename_hint="page.html",
        max_bytes=1_000_000,
        allowed_types=frozenset(),
    ))

    assert result["mime_type"] == "text/html"


# ── Filename derivation unit tests ─────────────────────────────


@pytest.mark.parametrize(
    ("hint", "url", "expected_prefix"),
    [
        ("report.pdf", "https://example.onion/doc", "report"),
        (None, "https://example.onion/files/data.csv", "data"),
        ("../../etc/passwd", "https://example.onion/x", "passwd"),
        ("good-name.txt", "https://example.onion/x", "good-name"),
    ],
)
def test_safe_download_filename_variations(hint, url, expected_prefix):
    name = TorBrowser._safe_download_filename(url, hint)
    assert "/" not in name
    assert ".." not in name
    assert expected_prefix in name or name.startswith("download_")


def test_safe_download_filename_fallback_for_empty_hint():
    name = TorBrowser._safe_download_filename("https://example.onion/", "")
    assert name.startswith("download_")


def test_safe_download_filename_fallback_for_special_chars_only():
    name = TorBrowser._safe_download_filename("https://example.onion/", "!!!")
    assert name.startswith("download_")
