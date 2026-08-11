"""Fallback and edge branches for HTML/forum extraction."""

import builtins

from bs4 import BeautifulSoup

from tor_mcp.extraction import (
    _extract_post_from_element,
    _extract_thread_from_element,
    extract_forum_posts,
    extract_forum_threads,
    extract_metadata,
    html_to_markdown,
)


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
