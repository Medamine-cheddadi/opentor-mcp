"""Schema-based structured data extraction from HTML pages."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger("tor-mcp.structured")


def extract_structured(
    html: str,
    schema: dict[str, str],
) -> dict:
    """Extract structured data from HTML using CSS selectors.

    *schema* maps field names to CSS selectors.  A selector ending with
    ``" *"`` (space-star) extracts **all** matching elements as a list;
    otherwise only the first match is returned.

    Returns::

        {
            "data": {field: value_or_list_or_null, ...},
            "missing_fields": [field_names_with_no_match],
            "extraction_quality": "good" | "partial" | "poor",
        }
    """
    if not schema:
        return {
            "data": {},
            "missing_fields": [],
            "extraction_quality": "good",
        }

    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, str | list[str] | None] = {}
    missing: list[str] = []

    for field, raw_selector in schema.items():
        selector, is_list = _parse_selector(raw_selector)
        try:
            if is_list:
                elements = soup.select(selector)
                if elements:
                    data[field] = [
                        el.get_text(strip=True) for el in elements
                    ]
                else:
                    data[field] = None
                    missing.append(field)
            else:
                element = soup.select_one(selector)
                if element is not None:
                    data[field] = element.get_text(strip=True)
                else:
                    data[field] = None
                    missing.append(field)
        except Exception as exc:
            raise ValueError(
                f"Invalid CSS selector for field '{field}': "
                f"'{raw_selector}'. {exc}"
            ) from exc

    total = len(schema)
    found = total - len(missing)
    if found == total:
        quality = "good"
    elif found > 0:
        quality = "partial"
    else:
        quality = "poor"

    return {
        "data": data,
        "missing_fields": missing,
        "extraction_quality": quality,
    }


def _parse_selector(raw: str) -> tuple[str, bool]:
    """Split a raw selector into (css_selector, is_list).

    A trailing ``" *"`` signals list extraction.
    """
    stripped = raw.strip()
    if stripped.endswith(" *"):
        return stripped[:-2].rstrip(), True
    return stripped, False
