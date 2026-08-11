"""Local CAPTCHA engine selection and OCR paths with in-memory fakes."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tor_mcp.captcha import CaptchaSolver


def run(coro):
    return asyncio.run(coro)


class Element:
    async def screenshot(self):
        return b"image-bytes"


class Page:
    def __init__(self, element="default"):
        self.element = Element() if element == "default" else element
        self.events = []

    async def query_selector(self, selector):
        self.events.append(("query", selector))
        return self.element

    async def fill(self, selector, value):
        self.events.append(("fill", selector, value))

    async def type(self, selector, value, **kwargs):
        self.events.append(("type", selector, value, kwargs))


def test_initialization_prefers_ddddocr(monkeypatch):
    engine = SimpleNamespace(classification=lambda image: " A7b ")
    module = ModuleType("ddddocr")
    module.DdddOcr = lambda **kwargs: engine
    monkeypatch.setitem(sys.modules, "ddddocr", module)

    solver = CaptchaSolver()

    assert solver.ocr_available == "ddddocr"
    assert solver._solve_locally(b"image") == "A7b"


def test_initialization_falls_back_to_pytesseract(monkeypatch):
    monkeypatch.setitem(sys.modules, "ddddocr", None)
    module = ModuleType("pytesseract")
    module.get_tesseract_version = lambda: "5.0"
    monkeypatch.setitem(sys.modules, "pytesseract", module)

    solver = CaptchaSolver()

    assert solver.ocr_available == "pytesseract"


def test_initialization_has_vision_fallback_when_engines_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "ddddocr", None)
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    solver = CaptchaSolver()

    assert solver.ocr_available is None
    assert solver._solve_locally(b"image") is None


def test_pytesseract_path_preprocesses_and_strips(monkeypatch):
    events = []

    class ImageValue:
        def convert(self, mode):
            events.append(("convert", mode))
            return self

    image_api = SimpleNamespace(open=lambda stream: ImageValue())
    pil = ModuleType("PIL")
    pil.Image = image_api
    pytesseract = ModuleType("pytesseract")
    pytesseract.image_to_string = lambda image, config: " Z9x \n"
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)

    solver = object.__new__(CaptchaSolver)
    solver._ocr_available = "pytesseract"
    solver._ddddocr_engine = None

    assert solver._solve_locally(b"png") == "Z9x"
    assert events == [("convert", "L")]


def test_ddddocr_empty_classification_returns_none():
    solver = object.__new__(CaptchaSolver)
    solver._ocr_available = "ddddocr"
    solver._ddddocr_engine = SimpleNamespace(classification=lambda image: "")

    assert solver._solve_locally(b"png") is None


def test_missing_captcha_element_is_reported():
    solver = object.__new__(CaptchaSolver)
    solver._ocr_available = None
    solver._ddddocr_engine = None

    with pytest.raises(ValueError, match="CAPTCHA element not found"):
        run(solver.get_captcha_image(Page(None), "#missing"))


def test_successful_local_guess_sets_strategy_and_auto_fills():
    solver = object.__new__(CaptchaSolver)
    solver._ocr_available = "ddddocr"
    solver._ddddocr_engine = SimpleNamespace(classification=lambda image: " r4T ")
    page = Page()

    captured = run(solver.get_captcha_image(page, "img"))
    result = run(solver.solve_captcha_auto(page, "img", "input"))

    assert captured["ocr_guess"] == "r4T"
    assert captured["strategy"] == "local_ddddocr"
    assert result["auto_filled"] is True
    assert result["filled_value"] == "r4T"
    assert ("fill", "input", "") in page.events
    assert ("type", "input", "r4T", {"delay": 50}) in page.events
