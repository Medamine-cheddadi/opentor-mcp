"""Tests for schema-based structured data extraction."""

import pytest

from tor_mcp.structured import extract_structured

FORUM_HTML = """
<html><body>
  <div class="post">
    <span class="author">alice</span>
    <span class="date">2026-08-10</span>
    <p class="content">First post content here.</p>
  </div>
  <div class="post">
    <span class="author">bob</span>
    <span class="date">2026-08-11</span>
    <p class="content">Second post content here.</p>
  </div>
  <div class="post">
    <span class="author">charlie</span>
    <span class="date">2026-08-12</span>
    <p class="content">Third post content here.</p>
  </div>
</body></html>
"""


def test_schema_with_valid_selectors_returns_all_fields():
    result = extract_structured(
        FORUM_HTML,
        {"author": ".post .author", "date": ".post .date"},
    )

    assert result["data"]["author"] == "alice"
    assert result["data"]["date"] == "2026-08-10"
    assert result["missing_fields"] == []
    assert result["extraction_quality"] == "good"


def test_list_selector_returns_all_matches():
    result = extract_structured(
        FORUM_HTML,
        {"authors": ".post .author *"},
    )

    assert result["data"]["authors"] == ["alice", "bob", "charlie"]
    assert result["missing_fields"] == []
    assert result["extraction_quality"] == "good"


def test_selector_matches_nothing_returns_null_and_missing():
    result = extract_structured(
        FORUM_HTML,
        {"title": "h1.title", "author": ".post .author"},
    )

    assert result["data"]["title"] is None
    assert result["data"]["author"] == "alice"
    assert result["missing_fields"] == ["title"]
    assert result["extraction_quality"] == "partial"


def test_selector_matches_multiple_without_star_returns_first():
    result = extract_structured(
        FORUM_HTML,
        {"author": ".post .author"},
    )

    assert result["data"]["author"] == "alice"


def test_empty_schema_returns_empty_data():
    result = extract_structured(FORUM_HTML, {})

    assert result["data"] == {}
    assert result["missing_fields"] == []
    assert result["extraction_quality"] == "good"


def test_all_fields_missing_returns_poor_quality():
    result = extract_structured(
        "<html><body><p>Nothing here.</p></body></html>",
        {"title": "h1", "author": ".author", "date": ".date"},
    )

    assert result["data"]["title"] is None
    assert result["data"]["author"] is None
    assert result["data"]["date"] is None
    assert result["missing_fields"] == ["title", "author", "date"]
    assert result["extraction_quality"] == "poor"


def test_list_selector_no_matches_returns_null():
    result = extract_structured(
        "<html><body></body></html>",
        {"tags": ".tag *"},
    )

    assert result["data"]["tags"] is None
    assert result["missing_fields"] == ["tags"]


def test_invalid_css_selector_raises_value_error():
    with pytest.raises(ValueError, match="Invalid CSS selector"):
        extract_structured(
            FORUM_HTML,
            {"bad": "[[[invalid"},
        )


def test_extract_structured_from_product_page():
    html = """
    <html><body>
      <h1 class="product-name">Widget Pro</h1>
      <span class="price">$29.99</span>
      <div class="description">A high-quality widget for professionals.</div>
      <ul class="features">
        <li class="feature">Durable construction</li>
        <li class="feature">Lightweight design</li>
        <li class="feature">Easy to use</li>
      </ul>
    </body></html>
    """

    result = extract_structured(html, {
        "name": "h1.product-name",
        "price": ".price",
        "description": ".description",
        "features": ".feature *",
    })

    assert result["data"]["name"] == "Widget Pro"
    assert result["data"]["price"] == "$29.99"
    assert result["data"]["features"] == [
        "Durable construction",
        "Lightweight design",
        "Easy to use",
    ]
    assert result["extraction_quality"] == "good"
