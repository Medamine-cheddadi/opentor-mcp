"""Privacy-oriented Playwright Firefox browser routed through Tor."""

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ViewportSize,
    async_playwright,
)

from tor_mcp.errors import classify_error

logger = logging.getLogger("tor-mcp.browser")

# A stable viewport reduces accidental variation without claiming Tor Browser parity.
TOR_VIEWPORT: ViewportSize = {"width": 1000, "height": 900}

# Privacy-oriented Firefox preferences. This is not a Tor Browser fingerprint.
STEALTH_PREFS = {
    # ── Core privacy controls ────────────────────────────────
    "privacy.resistFingerprinting": True,
    "privacy.firstparty.isolate": True,
    "privacy.trackingprotection.enabled": True,
    # Resolve hostnames at the SOCKS proxy rather than on the local network.
    "network.proxy.socks_remote_dns": True,
    "network.proxy.socks_version": 5,
    # ── Disable WebRTC (IP leak prevention) ─────────────────
    "media.peerconnection.enabled": False,
    "media.peerconnection.ice.no_host": True,
    "media.navigator.enabled": False,
    # ── Disable geolocation ─────────────────────────────────
    "geo.enabled": False,
    # ── Disable telemetry ───────────────────────────────────
    "toolkit.telemetry.enabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    # ── Disable safe browsing (phones home to Google) ───────
    "browser.safebrowsing.enabled": False,
    "browser.safebrowsing.malware.enabled": False,
    # ── Stable locale handling ───────────────────────────────
    "intl.regional_prefs.use_os_locales": False,
    # ── Canvas / WebGL fingerprinting protection ────────────
    "privacy.canvas.read.enabled": False,
    "webgl.disabled": True,
    # ── Disable battery API ─────────────────────────────────
    "dom.battery.enabled": False,
    # ── Disable device sensors ──────────────────────────────
    "device.sensors.enabled": False,
    # ── Disable media device enumeration ────────────────────
    "media.navigator.video.enabled": False,
    "media.navigator.audio.enabled": False,
    # ── Disable gamepad API ─────────────────────────────────
    "dom.gamepad.enabled": False,
    # ── Disable service workers ─────────────────────────────
    "dom.serviceWorkers.enabled": False,
    # ── Disable clipboard API ───────────────────────────────
    "dom.events.asyncClipboard.readText": False,
    "dom.events.asyncClipboard.clipboardItem": False,
}

SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def validate_storage_name(name: str, *, kind: str = "name") -> str:
    """Validate a collision-free, single-component storage name."""
    if not SAFE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid {kind}. Use 1-64 ASCII letters, numbers, '-' or '_', "
            "starting with a letter or number."
        )
    return name


def validate_navigation_url(url: str) -> str:
    """Allow only public HTTP(S) and onion destinations."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL.") from exc

    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not hostname:
        raise ValueError("Only absolute http:// and https:// URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials must not be embedded in URLs.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid URL port.")

    normalized_host = hostname.rstrip(".").lower()
    if not normalized_host.isascii():
        raise ValueError("Non-ASCII hostnames are not allowed.")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise ValueError("Local network destinations are not allowed.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            # Browsers accept legacy decimal/octal/hex and shortened IPv4 forms.
            # Canonicalize them without performing DNS so the policy matches the
            # destination the browser will actually request.
            address = ipaddress.ip_address(socket.inet_aton(normalized_host))
        except OSError:
            address = None
    if address and not address.is_global:
        raise ValueError("Private, loopback, and link-local IP addresses are not allowed.")
    return url


def _write_private_file(path: Path, data: bytes) -> None:
    """Write a regular owner-only file without following a final symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
    finally:
        os.close(descriptor)


class TorBrowser:
    """Playwright Firefox browser routed through a local Tor SOCKS5 proxy."""

    def __init__(
        self,
        tor_socks_port: int = 9050,
        tor_control_port: int = 9051,
        headless: bool = True,
        archives_dir: Path | None = None,
        ignore_https_errors: bool = False,
        tor_control_password: str | None = None,
    ):
        self.tor_socks_port = tor_socks_port
        self.tor_control_port = tor_control_port
        self.headless = headless
        self.ignore_https_errors = ignore_https_errors
        self.tor_control_password = tor_control_password
        self.archives_dir = archives_dir or Path("archives")
        self.archives_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.archives_dir.chmod(0o700)

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._launched = False
        self._launch_lock = asyncio.Lock()
        self.operation_lock = asyncio.Lock()

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if not self._context:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._context

    async def launch(self) -> None:
        """Launch privacy-oriented Firefox routed through Tor."""
        async with self._launch_lock:
            if self._launched:
                return
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.firefox.launch(
                    headless=self.headless,
                    firefox_user_prefs=STEALTH_PREFS,
                )
                self._context = await self._browser.new_context(
                    proxy={"server": f"socks5://127.0.0.1:{self.tor_socks_port}"},
                    viewport=TOR_VIEWPORT,
                    locale="en-US",
                    timezone_id="UTC",
                    ignore_https_errors=self.ignore_https_errors,
                )
                await self._context.route("**/*", self._guard_navigation_request)
                self._page = await self._context.new_page()
                self._launched = True
                logger.info(
                    "Firefox launched through Tor (socks5://127.0.0.1:%d)",
                    self.tor_socks_port,
                )
            except Exception:
                await self._cleanup_resources()
                raise

    async def ensure_launched(self) -> None:
        """Launch if not already launched."""
        if not self._launched:
            await self.launch()

    async def _guard_navigation_request(self, route: Any) -> None:
        """Apply the URL policy to redirects and page-initiated navigations."""
        request = route.request
        scheme = urlsplit(request.url).scheme.lower()
        if not request.is_navigation_request() and scheme not in ALLOWED_URL_SCHEMES:
            await route.continue_()
            return

        try:
            validate_navigation_url(request.url)
        except ValueError:
            logger.warning("Blocked an unsafe browser navigation request")
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    # ── Navigation ──────────────────────────────────────────────

    async def navigate(self, url: str, timeout: int = 60000) -> dict:
        """Navigate to a URL. Returns page info."""
        validate_navigation_url(url)
        await self.ensure_launched()
        try:
            response = await self.page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(1000)  # Let JS settle
            return {
                "url": self.page.url,
                "title": await self.page.title(),
                "status": response.status if response else None,
            }
        except Exception as e:
            return {
                "url": url,
                "title": None,
                "status": None,
                "error": classify_error(e),
            }

    async def go_back(self) -> dict:
        await self.ensure_launched()
        await self.page.go_back()
        return {"url": self.page.url, "title": await self.page.title()}

    async def go_forward(self) -> dict:
        await self.ensure_launched()
        await self.page.go_forward()
        return {"url": self.page.url, "title": await self.page.title()}

    async def refresh(self) -> dict:
        await self.ensure_launched()
        await self.page.reload()
        return {"url": self.page.url, "title": await self.page.title()}

    async def current_url(self) -> str:
        await self.ensure_launched()
        return self.page.url

    # ── Interaction ─────────────────────────────────────────────

    async def click(self, selector: str, timeout: int = 10000) -> str:
        """Click an element by CSS selector."""
        await self.ensure_launched()
        await self.page.click(selector, timeout=timeout)
        await self.page.wait_for_timeout(500)
        return f"Clicked: {selector}"

    async def type_text(self, selector: str, text: str, delay: int = 50) -> str:
        """Type text into an input field (human-like delay between keystrokes)."""
        await self.ensure_launched()
        await self.page.fill(selector, "")  # Clear first
        await self.page.type(selector, text, delay=delay)
        return f"Typed into: {selector}"

    async def press_key(self, key: str) -> str:
        """Press a keyboard key (Enter, Tab, etc.)."""
        await self.ensure_launched()
        await self.page.keyboard.press(key)
        return f"Pressed: {key}"

    async def select_option(self, selector: str, value: str) -> str:
        """Select a dropdown option."""
        await self.ensure_launched()
        await self.page.select_option(selector, value)
        return f"Selected {value} in {selector}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        """Scroll the page. Direction: up/down/top/bottom."""
        await self.ensure_launched()
        if direction == "top":
            await self.page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "up":
            await self.page.evaluate("amount => window.scrollBy(0, -amount)", amount)
        elif direction == "down":
            await self.page.evaluate("amount => window.scrollBy(0, amount)", amount)
        else:
            raise ValueError("Direction must be one of: up, down, top, bottom.")
        return f"Scrolled {direction} by {amount}px"

    async def wait_for(self, selector: str, timeout: int = 10000) -> bool:
        """Wait for an element to appear."""
        await self.ensure_launched()
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    # ── Reading ─────────────────────────────────────────────────

    async def screenshot(self, full_page: bool = False) -> bytes:
        """Take a screenshot, returns PNG bytes."""
        await self.ensure_launched()
        return await self.page.screenshot(full_page=full_page)

    async def screenshot_element(self, selector: str) -> bytes:
        """Screenshot a specific element, returns PNG bytes."""
        await self.ensure_launched()
        element = await self.page.query_selector(selector)
        if not element:
            raise ValueError(f"Element not found: {selector}")
        return await element.screenshot()

    async def get_content(self) -> str:
        """Get the page's inner text content."""
        await self.ensure_launched()
        return await self.page.inner_text("body")

    async def get_html(self) -> str:
        """Get raw HTML of the page."""
        await self.ensure_launched()
        return await self.page.content()

    async def get_links(self) -> list[dict]:
        """Extract all links from the page."""
        await self.ensure_launched()
        return await self.page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: a.innerText.trim().substring(0, 200),
                href: a.href,
            })).filter(l => l.href && l.text)
        """)

    async def get_page_info(self) -> dict:
        """Get current page metadata."""
        await self.ensure_launched()
        return await self.page.evaluate("""
            () => ({
                url: window.location.href,
                title: document.title,
                description: document.querySelector('meta[name="description"]')?.content || null,
                lang: document.documentElement.lang || null,
                links_count: document.querySelectorAll('a[href]').length,
                images_count: document.querySelectorAll('img').length,
                forms_count: document.querySelectorAll('form').length,
                inputs_count: document.querySelectorAll('input, textarea, select').length,
            })
        """)

    async def evaluate_js(self, script: str) -> str:
        """Execute JavaScript on the page and return result."""
        await self.ensure_launched()
        result = await self.page.evaluate(script)
        return str(result)

    async def query_elements(self, selector: str) -> list[dict]:
        """Query elements and return their text + attributes."""
        await self.ensure_launched()
        return await self.page.evaluate(
            """
            selector => Array.from(document.querySelectorAll(selector)).map((el, i) => ({
                index: i,
                tag: el.tagName.toLowerCase(),
                text: el.innerText?.trim().substring(0, 500) || '',
                href: el.href || null,
                src: el.src || null,
                type: el.type || null,
                name: el.name || null,
                id: el.id || null,
                class: el.className || null,
            }))
        """,
            selector,
        )

    # ── Cookies / Session ───────────────────────────────────────

    async def get_cookies(self) -> list[Any]:
        """Get all cookies from current context."""
        await self.ensure_launched()
        return await self.context.cookies()

    async def set_cookies(self, cookies: Sequence[Any]) -> None:
        """Set cookies on the current context."""
        await self.ensure_launched()
        await self.context.add_cookies(cookies)

    async def clear_cookies(self) -> None:
        """Clear all cookies."""
        await self.ensure_launched()
        await self.context.clear_cookies()

    # ── Tor Control ─────────────────────────────────────────────

    def _signal_newnym(self) -> None:
        """Authenticate to Tor control and request NEWNYM in a worker thread."""
        import time

        from stem import Signal
        from stem.control import Controller

        with Controller.from_port(port=str(self.tor_control_port)) as controller:
            controller.authenticate(password=self.tor_control_password)
            wait_seconds = controller.get_newnym_wait()
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            stem_signal: Any = Signal
            controller.signal(stem_signal.NEWNYM)

    async def rotate_circuit(self) -> str:
        """Rotate Tor circuit (NEWNYM) WITHOUT clearing cookies.

        Use this for anti-rate-limiting — keeps session cookies intact
        so DDoS protection clearance is preserved.
        """
        try:
            await asyncio.to_thread(self._signal_newnym)
            return "Tor circuit rotated. Cookies preserved."
        except Exception:
            logger.exception("Tor NEWNYM request failed")
            return (
                "Tor circuit rotation failed. Check the authenticated control-port configuration."
            )

    async def new_identity(self) -> str:
        """Full identity reset: new Tor circuit + clear all cookies."""
        circuit_rotated = False
        try:
            await asyncio.to_thread(self._signal_newnym)
            circuit_rotated = True
        except Exception:
            logger.exception("Tor NEWNYM request failed during identity reset")
        finally:
            await self.clear_cookies()

        if circuit_rotated:
            return "New Tor identity acquired. Cookies cleared."
        return "Circuit rotation failed, but browser cookies were cleared."

    async def check_tor_connection(self) -> dict:
        """Verify Tor connectivity by checking exit IP."""
        await self.ensure_launched()
        temporary_page = await self.context.new_page()
        try:
            await temporary_page.goto("https://check.torproject.org/api/ip", timeout=30000)
            content = await temporary_page.inner_text("body")
            data = json.loads(content)
            return {
                "connected": data.get("IsTor", False),
                "ip": data.get("IP", "unknown"),
            }
        except Exception:
            logger.exception("Tor connection verification failed")
            return {
                "connected": False,
                "ip": None,
                "error": "Tor connection verification failed.",
            }
        finally:
            await temporary_page.close()

    # ── Archive ─────────────────────────────────────────────────

    async def archive_page(self, name: str) -> str:
        """Save current page content and screenshot to archives."""
        validate_storage_name(name, kind="archive name")
        archive_root = self.archives_dir.resolve()
        archive_dir = self.archives_dir / name
        if archive_dir.is_symlink():
            raise ValueError("Archive destination must not be a symlink.")
        resolved_archive = archive_dir.resolve(strict=False)
        if resolved_archive.parent != archive_root:
            raise ValueError("Archive destination escapes the configured archive root.")

        await self.ensure_launched()
        archive_dir.mkdir(parents=False, exist_ok=True, mode=0o700)
        archive_dir.chmod(0o700)

        # Save HTML
        html = await self.get_html()
        await asyncio.to_thread(
            _write_private_file, archive_dir / "page.html", html.encode("utf-8")
        )

        # Save text content
        text = await self.get_content()
        await asyncio.to_thread(
            _write_private_file, archive_dir / "content.txt", text.encode("utf-8")
        )

        # Save screenshot
        screenshot = await self.screenshot(full_page=True)
        await asyncio.to_thread(_write_private_file, archive_dir / "screenshot.png", screenshot)

        # Save metadata
        info = await self.get_page_info()
        await asyncio.to_thread(
            _write_private_file,
            archive_dir / "metadata.json",
            json.dumps(info, indent=2).encode("utf-8"),
        )

        return f"Archived to {archive_dir}"

    # ── Lifecycle ───────────────────────────────────────────────

    async def close(self) -> None:
        """Close browser and cleanup."""
        async with self._launch_lock:
            await self._cleanup_resources()

    async def _cleanup_resources(self) -> None:
        """Close any fully or partially initialized Playwright resources."""
        context, browser, playwright = self._context, self._browser, self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._launched = False

        if context:
            await context.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        if context or browser or playwright:
            logger.info("Tor browser closed.")
