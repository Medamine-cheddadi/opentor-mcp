"""Free CAPTCHA solving — local OCR + vision relay to the calling agent."""

import base64
import logging

logger = logging.getLogger("tor-mcp.captcha")


class CaptchaSolver:
    """Solve CAPTCHAs for free using local OCR and agent vision relay.

    Strategy (ordered by preference):
    1. Vision relay: return the CAPTCHA image to a vision-capable MCP client.
    2. ddddocr: local ML model trained specifically on CAPTCHAs (if installed).
    3. pytesseract: general-purpose OCR fallback (if installed).
    """

    def __init__(self):
        self._ddddocr_engine = None
        self._ocr_available: str | None = None
        self._init_ocr()

    def _init_ocr(self) -> None:
        """Try to load a local OCR engine."""
        # Try ddddocr first (purpose-built for CAPTCHAs)
        try:
            import ddddocr  # pyright: ignore[reportMissingImports]

            self._ddddocr_engine = ddddocr.DdddOcr(show_ad=False)
            self._ocr_available = "ddddocr"
            logger.info("CAPTCHA OCR engine: ddddocr")
            return
        except ImportError:
            pass

        # Fallback to pytesseract
        try:
            import pytesseract  # pyright: ignore[reportMissingImports]

            pytesseract.get_tesseract_version()
            self._ocr_available = "pytesseract"
            logger.info("CAPTCHA OCR engine: pytesseract")
            return
        except (ImportError, Exception):
            pass

        logger.info(
            "No local OCR available. Vision relay (agent reads CAPTCHA) is primary strategy."
        )

    @property
    def ocr_available(self) -> str | None:
        return self._ocr_available

    async def get_captcha_image(self, page, selector: str) -> dict:
        """Capture a CAPTCHA element and return it for the agent to read.

        Returns a dict with:
        - image_base64: the CAPTCHA image as base64 PNG
        - ocr_guess: local OCR attempt (if available), or None
        - strategy: which method produced the guess

        A vision-capable MCP client receives the image and can read the
        CAPTCHA — this is the primary strategy.
        """
        # Try to find the element
        element = await page.query_selector(selector)
        if not element:
            raise ValueError(f"CAPTCHA element not found: {selector}")

        # Screenshot the element
        image_bytes = await element.screenshot()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Attempt local OCR as a hint
        ocr_guess = None
        strategy = "vision_relay"

        if self._ocr_available:
            try:
                ocr_guess = self._solve_locally(image_bytes)
                strategy = f"local_{self._ocr_available}"
                logger.info("Local OCR guess: %s", ocr_guess)
            except Exception as e:
                logger.warning("Local OCR failed: %s", e)

        return {
            "image_base64": image_b64,
            "ocr_guess": ocr_guess,
            "strategy": strategy,
            "instructions": (
                "The CAPTCHA image is attached. Read the text in the image. "
                "If an OCR guess is provided, verify it against what you see. "
                "Then call tor_type with the CAPTCHA input selector and the text you read."
            ),
        }

    def _solve_locally(self, image_bytes: bytes) -> str | None:
        """Attempt to solve CAPTCHA with local OCR."""
        if self._ocr_available == "ddddocr" and self._ddddocr_engine:
            result = self._ddddocr_engine.classification(image_bytes)
            return result.strip() if result else None

        if self._ocr_available == "pytesseract":
            import io

            import pytesseract  # pyright: ignore[reportMissingImports]
            from PIL import Image  # pyright: ignore[reportMissingImports]

            image = Image.open(io.BytesIO(image_bytes))
            # Preprocess for better OCR: convert to grayscale, increase contrast
            image = image.convert("L")
            tesseract_config = (
                "--psm 7 -c tessedit_char_whitelist="
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            )
            result = pytesseract.image_to_string(
                image,
                config=tesseract_config,
            )
            return result.strip() if result else None

        return None

    async def solve_captcha_auto(
        self,
        page,
        captcha_img_selector: str,
        captcha_input_selector: str,
    ) -> dict:
        """Full auto-solve: capture CAPTCHA, OCR it, type the answer.

        Only works if local OCR is available. Otherwise returns the image
        for the agent to solve via vision relay.
        """
        result = await self.get_captcha_image(page, captcha_img_selector)

        if result["ocr_guess"]:
            # Auto-fill the CAPTCHA
            await page.fill(captcha_input_selector, "")
            await page.type(captcha_input_selector, result["ocr_guess"], delay=50)
            result["auto_filled"] = True
            result["filled_value"] = result["ocr_guess"]
        else:
            result["auto_filled"] = False
            result["instructions"] = (
                "Could not auto-solve. The CAPTCHA image is attached. "
                "Read the text and call tor_type to fill it in."
            )

        return result
