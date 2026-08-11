"""CAPTCHA relay behavior independent of any real browser or OCR service."""

import asyncio
import base64

from tor_mcp.captcha import CaptchaSolver


def run(coro):
    return asyncio.run(coro)


class _Element:
    def __init__(self, image_bytes=b"captcha-png"):
        self.image_bytes = image_bytes

    async def screenshot(self):
        return self.image_bytes


class _Page:
    def __init__(self, element):
        self.element = element

    async def query_selector(self, selector):
        return self.element


def _solver_without_initialization():
    solver = object.__new__(CaptchaSolver)
    solver._ddddocr_engine = None
    solver._ocr_available = None
    return solver


def test_get_captcha_returns_png_for_vision_when_no_ocr_is_installed():
    solver = _solver_without_initialization()

    result = run(solver.get_captcha_image(_Page(_Element()), ".captcha"))

    assert base64.b64decode(result["image_base64"]) == b"captcha-png"
    assert result["ocr_guess"] is None
    assert result["strategy"] == "vision_relay"


def test_ocr_exception_does_not_discard_the_captcha_image():
    solver = _solver_without_initialization()
    solver._ocr_available = "ddddocr"

    def fail_ocr(image_bytes):
        raise RuntimeError("bad OCR model")

    solver._solve_locally = fail_ocr

    result = run(solver.get_captcha_image(_Page(_Element(b"retain-me")), "#captcha"))

    assert base64.b64decode(result["image_base64"]) == b"retain-me"
    assert result["ocr_guess"] is None
    assert result["strategy"] == "vision_relay"


def test_auto_solver_retains_image_when_no_guess_can_be_produced():
    solver = _solver_without_initialization()

    result = run(solver.solve_captcha_auto(_Page(_Element()), "img", "input"))

    assert base64.b64decode(result["image_base64"]) == b"captcha-png"
    assert result["auto_filled"] is False
    assert "attached" in result["instructions"].lower()
