"""Fallback and edge branches for HTML/forum extraction."""

import asyncio
import builtins

import pytest
from bs4 import BeautifulSoup

from tor_mcp.extraction import (
    _extract_post_from_element,
    _extract_thread_from_element,
    extract_content,
    extract_forum_posts,
    extract_forum_threads,
    extract_metadata,
    html_to_markdown,
    score_quality,
)


def run(coro):
    return asyncio.run(coro)


def test_html_to_markdown_falls_back_to_visible_text(monkeypatch):
    real_import = builtins.__import__

    def import_without_markdownify(name, *args, **kwargs):
        if name == "markdownify":
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_markdownify)

    result = html_to_markdown(
        "<nav>menu</nav><script>bad()</script><style>x{}</style><p>Visible text</p>"
    )

    assert result == "Visible text"


def test_thread_extraction_uses_secondary_card_strategy():
    threads = extract_forum_threads(
        '<div class="card"><a href="/topic">A secondary discussion</a></div>'
    )

    assert threads[0]["title"] == "A secondary discussion"


def test_thread_extraction_uses_generic_repeated_link_fallback():
    html = """
    <section>
      <a href="/1">First sufficiently long topic</a>
      <a href="/2">Second sufficiently long topic</a>
      <a href="/3">Third sufficiently long topic</a>
      <a href="/nav">short</a>
    </section>
    """

    threads = extract_forum_threads(html)

    assert [thread["url"] for thread in threads] == ["/1", "/2", "/3"]
    assert all(thread["author"] is None for thread in threads)


def test_thread_element_rejects_missing_or_too_short_titles():
    no_link = BeautifulSoup("<div>plain</div>", "html.parser").div
    short = BeautifulSoup('<div><a href="/x">x</a></div>', "html.parser").div

    assert _extract_thread_from_element(no_link) is None
    assert _extract_thread_from_element(short) is None


def test_thread_date_falls_back_to_title_then_text():
    titled = BeautifulSoup(
        '<div><a href="/x">Useful title</a><time title="Yesterday">shown</time></div>',
        "html.parser",
    ).div
    textual = BeautifulSoup(
        '<div><a href="/y">Another title</a><span class="date">Today</span></div>',
        "html.parser",
    ).div

    assert _extract_thread_from_element(titled)["date"] == "Yesterday"
    assert _extract_thread_from_element(textual)["date"] == "Today"


def test_post_element_rejects_empty_content_and_date_uses_text():
    empty = BeautifulSoup('<article class="post">x</article>', "html.parser").article
    dated = BeautifulSoup(
        '<article class="post"><span class="date">Today</span> substantive text</article>',
        "html.parser",
    ).article

    assert _extract_post_from_element(empty) is None
    assert _extract_post_from_element(dated)["date"] == "Today"


def test_post_fallback_returns_empty_list_for_empty_page():
    assert extract_forum_posts("<html><body></body></html>") == []


def test_metadata_uses_open_graph_description_and_handles_missing_fields():
    metadata = extract_metadata('<meta property="og:description" content="Social summary">')

    assert metadata["description"] == "Social summary"
    assert metadata["title"] is None
    assert metadata["canonical"] is None


# ── Quality scoring tests ──────────────────────────────────────


def test_score_quality_empty_text_returns_zero():
    assert score_quality("") == 0.0
    assert score_quality("   ") == 0.0


def test_score_quality_rich_content_scores_high():
    rich = (
        "# Main Heading\n\n"
        "This is a detailed paragraph with substantial content that describes "
        "the topic thoroughly and provides meaningful information to readers.\n\n"
        "## Subheading\n\n"
        "- First item in the list\n"
        "- Second item in the list\n"
        "- Third item in the list\n\n"
        "[Link text](https://example.com)\n"
        "[Another link](https://example.com/page)\n\n"
        "More paragraph content follows to ensure we have enough text to "
        "demonstrate that the scoring function recognizes high-quality output."
    )
    score = score_quality(rich)

    assert score >= 0.6, f"Rich content scored {score}, expected >= 0.6"


def test_score_quality_short_text_scores_low():
    score = score_quality("Hi")

    assert score < 0.3, f"Short text scored {score}, expected < 0.3"


def test_score_quality_script_heavy_html_penalizes_boilerplate():
    boilerplate_html = (
        "<script>" + "x" * 2000 + "</script>"
        "<p>Tiny content</p>"
    )
    score = score_quality("Tiny content", html=boilerplate_html)
    clean_score = score_quality(
        "Tiny content",
        html="<p>Tiny content</p>",
    )

    assert score < clean_score, (
        f"Boilerplate score {score} should be less than clean score {clean_score}"
    )


def test_score_quality_distinguishes_good_from_poor():
    good_text = (
        "# Introduction\n\n"
        "This document covers the complete architecture of the system.\n\n"
        "## Components\n\n"
        "- [Database](https://db.example.com) handles persistence\n"
        "- [API](https://api.example.com) handles requests\n"
        "- [Frontend](https://app.example.com) handles the UI\n\n"
        "## Conclusion\n\n"
        "The system is designed for reliability and scalability."
    )
    poor_text = "var x = function() { return null; }"

    good_score = score_quality(good_text)
    poor_score = score_quality(poor_text)

    assert good_score > poor_score, (
        f"Good score {good_score} should exceed poor score {poor_score}"
    )
    assert good_score >= 0.6
    assert poor_score < 0.3


@pytest.mark.parametrize(
    "text,min_score,max_score",
    [
        ("", 0.0, 0.0),
        ("x", 0.0, 0.2),
        ("A medium-length paragraph with some content.", 0.1, 0.5),
    ],
    ids=["empty", "single_char", "medium_paragraph"],
)
def test_score_quality_ranges(text, min_score, max_score):
    score = score_quality(text)

    assert min_score <= score <= max_score, (
        f"Score {score} outside expected range [{min_score}, {max_score}]"
    )


# ── Extraction chain with markdownify unavailable ──────────────


def test_extract_content_falls_through_when_markdownify_missing(monkeypatch):
    real_import = builtins.__import__

    def import_without_markdownify(name, *args, **kwargs):
        if name == "markdownify":
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_markdownify)

    html = """
    <html><body>
      <h1>Important Title</h1>
      <p>A paragraph with real content that should be extracted even
      without markdownify being available as a conversion library.</p>
      <ul>
        <li>First list item with useful info</li>
        <li>Second list item with useful info</li>
      </ul>
      <p>Another paragraph providing additional context and information
      about the topic being discussed in this web page.</p>
    </body></html>
    """

    result = extract_content(html)

    assert result["content"]  # non-empty
    assert "Important Title" in result["content"]
    assert result["strategy"] in ("beautifulsoup", "raw_text")
    assert result["quality"] in ("good", "fair", "poor")


# ── Integration: tor_read_page includes extraction_quality ─────


def test_tor_read_page_includes_extraction_quality_metadata(monkeypatch):
    import tor_mcp.server as server

    class FakeBrowser:
        async def get_html(self):
            return """
            <html><body>
              <h1>Test Page Title</h1>
              <p>This is a real page with meaningful content that should be
              extracted properly and scored as reasonable quality output.</p>
              <h2>Details Section</h2>
              <p>More detailed content follows here to ensure that the
              extraction chain produces adequate quality results.</p>
              <ul>
                <li>Important detail one</li>
                <li>Important detail two</li>
              </ul>
            </body></html>
            """

        async def current_url(self):
            return "https://example.onion/page"

        async def get_page_info(self):
            return {"title": "Test Page Title", "url": "https://example.onion/page"}

    monkeypatch.setattr(server, "get_browser", lambda: FakeBrowser())

    result = run(server.tor_read_page(max_chars=20000))

    assert "extraction_quality:" in result
    assert "Test Page Title" in result


def test_tor_read_page_surfaces_quality_warning_for_poor_content(monkeypatch):
    import tor_mcp.server as server

    class EmptyBrowser:
        async def get_html(self):
            return "<html><body><script>var x = 1;</script></body></html>"

        async def current_url(self):
            return "https://example.onion"

        async def get_page_info(self):
            return {"title": "Empty", "url": "https://example.onion"}

    monkeypatch.setattr(server, "get_browser", lambda: EmptyBrowser())

    result = run(server.tor_read_page(max_chars=20000))

    assert "extraction_quality: poor" in result
    assert "quality_warning:" in result
