"""Representative extraction coverage retained alongside the security suite."""

from tor_mcp.extraction import (
    extract_forum_posts,
    extract_forum_threads,
    extract_metadata,
    html_to_markdown,
)


def test_html_to_markdown_keeps_headings_and_formatted_content():
    html = """
    <html><body>
      <nav>Ignore navigation</nav>
      <h1>Useful heading</h1>
      <p>Hello <strong>reader</strong>.</p>
      <script>alert('ignore script')</script>
      <style>.hidden { display: none }</style>
    </body></html>
    """

    markdown = html_to_markdown(html)

    assert "Useful heading" in markdown
    assert "Hello" in markdown and "reader" in markdown


def test_extract_metadata_handles_standard_open_graph_and_canonical_fields():
    html = """
    <html><head>
      <title> Example page </title>
      <meta name="description" content="Standard description">
      <meta name="keywords" content="one, two">
      <meta name="author" content="Alice">
      <meta property="og:title" content="Social title">
      <meta property="og:image" content="https://example.com/card.png">
      <link rel="canonical" href="https://example.com/canonical">
    </head></html>
    """

    metadata = extract_metadata(html)

    assert metadata == {
        "title": "Example page",
        "description": "Standard description",
        "keywords": "one, two",
        "author": "Alice",
        "og_title": "Social title",
        "og_image": "https://example.com/card.png",
        "canonical": "https://example.com/canonical",
    }


def test_extract_forum_threads_reads_common_listing_fields():
    html = """
    <ul class="threadlist">
      <li class="thread">
        <a href="/thread/one">First discussion</a>
        <span class="author">alice</span>
        <time datetime="2026-08-10T12:00:00Z">yesterday</time>
        <span class="replies">12 replies</span>
        <p class="preview">An informative preview.</p>
      </li>
      <li class="thread">
        <a href="/thread/two">Second discussion</a>
        <span class="author">bob</span>
      </li>
    </ul>
    """

    threads = extract_forum_threads(html)

    assert [thread["title"] for thread in threads] == [
        "First discussion",
        "Second discussion",
    ]
    assert threads[0]["url"] == "/thread/one"
    assert threads[0]["author"] == "alice"
    assert threads[0]["date"] == "2026-08-10T12:00:00Z"
    assert threads[0]["replies"] == "12 replies"
    assert threads[0]["snippet"] == "An informative preview."


def test_extract_forum_posts_reads_author_date_and_content():
    html = """
    <main>
      <article class="message">
        <span class="username">alice</span>
        <time datetime="2026-08-10T12:00:00Z">yesterday</time>
        <div class="messageContent">The first substantive answer.</div>
      </article>
      <article class="message">
        <span class="username">bob</span>
        <div class="messageContent">The second substantive answer.</div>
      </article>
    </main>
    """

    posts = extract_forum_posts(html)

    assert len(posts) >= 2
    assert posts[0]["author"] == "alice"
    assert posts[0]["date"] == "2026-08-10T12:00:00Z"
    assert "first substantive answer" in posts[0]["content"].lower()
    assert any(post["author"] == "bob" for post in posts)


def test_extract_forum_posts_falls_back_to_page_text():
    posts = extract_forum_posts(
        "<html><body><div>A plain page without forum markup.</div></body></html>"
    )

    assert posts == [
        {
            "author": None,
            "date": None,
            "content": "A plain page without forum markup.",
            "index": 0,
        }
    ]
