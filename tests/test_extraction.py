"""Representative extraction coverage retained alongside the security suite."""

from tor_mcp.extraction import (
    classify_page,
    extract_content,
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


# ── Fallback chain tests ──────────────────────────────────────


def test_extract_content_good_html_stops_at_first_strategy():
    html = """
    <html><body>
      <h1>Welcome to the Guide</h1>
      <p>This is a comprehensive guide to understanding the topic at hand.
      It covers many important aspects that you should know about.</p>
      <h2>Section One</h2>
      <p>The first section introduces the core concepts and foundational
      principles that underpin the rest of the material presented here.</p>
      <ul>
        <li>First important point about the topic</li>
        <li>Second important point about the topic</li>
        <li>Third important point about the topic</li>
      </ul>
      <h2>Section Two</h2>
      <p>Building on section one, we explore more advanced themes. This
      section dives deeper into practical applications and use cases.</p>
      <a href="/more">Read more about this topic</a>
    </body></html>
    """

    result = extract_content(html)

    assert result["quality"] == "good"
    assert result["strategy"] == "markdownify"
    assert result["quality_warning"] is None
    assert "Welcome to the Guide" in result["content"]


def test_extract_content_minimal_html_selects_best_result():
    html = "<html><body><p>Short.</p></body></html>"

    result = extract_content(html)

    assert result["quality"] in ("fair", "poor")
    assert result["content"]  # non-empty — best effort returned
    assert "Short" in result["content"]


def test_extract_content_empty_html_returns_poor_with_warning():
    html = "<html><body><script>var x = 1;</script></body></html>"

    result = extract_content(html)

    assert result["quality"] == "poor"
    assert result["quality_warning"] is not None


def test_extract_content_complex_table_preserves_structure():
    html = """
    <html><body>
      <h1>Data Report</h1>
      <p>Below is the quarterly data summary for the reporting period.</p>
      <table>
        <tr><th>Name</th><th>Value</th><th>Change</th></tr>
        <tr><td>Revenue</td><td>$1.2M</td><td>+15%</td></tr>
        <tr><td>Users</td><td>50,000</td><td>+22%</td></tr>
        <tr><td>Retention</td><td>85%</td><td>+3%</td></tr>
      </table>
      <p>These results demonstrate strong growth across all key metrics
      for the current reporting period compared to the previous one.</p>
    </body></html>
    """

    result = extract_content(html)

    assert "Revenue" in result["content"]
    assert "$1.2M" in result["content"]
    assert "Users" in result["content"]


def test_extract_content_nested_lists_preserved():
    html = """
    <html><body>
      <h1>Project Overview</h1>
      <p>This project consists of several major components and subsystems
      that work together to deliver the final product.</p>
      <ul>
        <li>Frontend
          <ul>
            <li>React components for the user interface</li>
            <li>CSS modules for styling and theming</li>
          </ul>
        </li>
        <li>Backend
          <ul>
            <li>API server handling requests</li>
            <li>Database layer for persistence</li>
          </ul>
        </li>
      </ul>
    </body></html>
    """

    result = extract_content(html)

    assert "Frontend" in result["content"]
    assert "React components" in result["content"]
    assert "Backend" in result["content"]
    assert "API server" in result["content"]


def test_extract_content_images_include_alt_text():
    html = """
    <html><body>
      <h1>Photo Gallery</h1>
      <p>A collection of photographs from the recent expedition to document
      the wildlife and natural landscapes of the region.</p>
      <img src="/photo1.jpg" alt="Sunset over the mountain range">
      <img src="/photo2.jpg" alt="River flowing through the valley">
      <p>These photographs capture the beauty and diversity of the natural
      environment found in this remarkable conservation area.</p>
    </body></html>
    """

    result = extract_content(html)

    assert "Sunset over the mountain range" in result["content"]
    assert "River flowing through the valley" in result["content"]


def test_extract_content_all_empty_returns_empty_with_poor():
    result = extract_content("")

    assert result["content"] == ""
    assert result["quality"] == "poor"
    assert result["quality_warning"] is not None


def test_extract_content_never_raises():
    """Extraction must always return a result, never raise."""
    for html in [
        "",
        "<html></html>",
        "not even html",
        "<script>" + "x" * 10000 + "</script>",
        "<html><body>" + "<div>" * 100 + "</div>" * 100 + "</body></html>",
    ]:
        result = extract_content(html)
        assert "quality" in result
        assert "content" in result
        assert result["quality"] in ("good", "fair", "poor")


# ── Content-type classification tests ─────────────────────────


def test_classify_page_article_with_article_tag_and_long_text():
    html = """
    <html><body>
      <article>
        <h1>Understanding Tor Network Architecture</h1>
        <p>The Tor network is a sophisticated system designed to provide
        anonymity on the internet. It works by routing traffic through a
        series of relays, each of which only knows the previous and next
        hop in the circuit. This multi-layered encryption approach ensures
        that no single relay can determine both the origin and destination
        of the traffic being routed through the network.</p>
        <h2>How Circuits Work</h2>
        <p>When a user connects to the Tor network, their client software
        selects a path through three relays: a guard node, a middle relay,
        and an exit relay. Each relay peels off one layer of encryption,
        revealing only the address of the next relay in the chain. This is
        why the system is called "onion routing" — each layer is removed
        like peeling an onion.</p>
        <h2>Guard Nodes</h2>
        <p>Guard nodes are the first point of contact when connecting to
        the Tor network. They are selected from a set of long-running,
        stable relays to reduce the risk of an adversary controlling the
        entry point to the circuit.</p>
      </article>
    </body></html>
    """
    assert classify_page(html) == "article"


def test_classify_page_forum_with_post_containers_and_metadata():
    html = """
    <html><body>
      <div class="forum">
        <div class="thread">
          <a href="/t/1">First thread topic</a>
          <span class="author">alice</span>
          <time datetime="2026-08-10">Aug 10</time>
          <span class="replies">12</span>
        </div>
        <div class="thread">
          <a href="/t/2">Second thread topic</a>
          <span class="author">bob</span>
          <time datetime="2026-08-11">Aug 11</time>
          <span class="replies">5</span>
        </div>
        <div class="thread">
          <a href="/t/3">Third thread topic</a>
          <span class="author">carol</span>
          <time datetime="2026-08-12">Aug 12</time>
        </div>
        <div class="thread">
          <a href="/t/4">Fourth thread topic</a>
          <span class="author">dave</span>
          <time datetime="2026-08-12">Aug 12</time>
        </div>
        <div class="thread">
          <a href="/t/5">Fifth thread topic</a>
          <span class="author">eve</span>
          <time datetime="2026-08-13">Aug 13</time>
        </div>
      </div>
    </body></html>
    """
    assert classify_page(html) == "forum"


def test_classify_page_login_form_with_password_input():
    html = """
    <html><body>
      <h1>Sign In</h1>
      <form action="/login" method="post">
        <label for="user">Username</label>
        <input type="text" id="user" name="username">
        <label for="pass">Password</label>
        <input type="password" id="pass" name="password">
        <button type="submit">Log in</button>
      </form>
    </body></html>
    """
    assert classify_page(html) == "login_form"


def test_classify_page_directory_listing_with_repeated_links():
    html = """
    <html><body>
      <h1>Index of /files</h1>
      <ul>
        <li><a href="report-q1.pdf">report-q1.pdf</a></li>
        <li><a href="report-q2.pdf">report-q2.pdf</a></li>
        <li><a href="report-q3.pdf">report-q3.pdf</a></li>
        <li><a href="report-q4.pdf">report-q4.pdf</a></li>
        <li><a href="summary.doc">summary.doc</a></li>
        <li><a href="archive.zip">archive.zip</a></li>
        <li><a href="notes.txt">notes.txt</a></li>
      </ul>
    </body></html>
    """
    assert classify_page(html) == "directory_listing"


def test_classify_page_search_results():
    html = """
    <html><body>
      <form action="/search">
        <input type="search" name="q" value="tor network">
        <button type="submit">Search</button>
      </form>
      <div class="search-results">
        <div class="result">
          <a href="/r/1">Tor Project Official Site</a>
          <p>The official website of the Tor Project.</p>
        </div>
        <div class="result">
          <a href="/r/2">Tor Browser Download</a>
          <p>Download the Tor Browser for secure browsing.</p>
        </div>
        <div class="result">
          <a href="/r/3">How Tor Works</a>
          <p>An explanation of Tor's onion routing protocol.</p>
        </div>
      </div>
    </body></html>
    """
    assert classify_page(html) == "search_results"


def test_classify_page_error_page():
    html = """
    <html><head><title>404 Not Found</title></head>
    <body>
      <h1>404 Not Found</h1>
      <p>The page you requested could not be found.</p>
    </body></html>
    """
    assert classify_page(html) == "error_page"


def test_classify_page_mixed_signals_most_confident_wins():
    """Page with both an article and a form — article signal is stronger."""
    html = """
    <html><body>
      <article>
        <h1>Comprehensive Guide to Privacy</h1>
        <p>This extensive guide covers everything you need to know about
        maintaining your privacy online. We explore various tools and
        techniques that are available to protect your digital identity
        from tracking and surveillance across the modern internet.</p>
        <h2>Section One: Browser Privacy</h2>
        <p>Your browser is the primary vector through which your browsing
        habits and personal information can be collected and analyzed by
        third parties operating advertising and tracking networks.</p>
        <h2>Section Two: Network Privacy</h2>
        <p>Beyond the browser, network-level privacy measures ensure that
        your internet service provider and other intermediaries cannot
        easily monitor or log your online activities and connections.</p>
      </article>
      <form action="/subscribe">
        <input type="text" name="email" placeholder="Newsletter signup">
        <button>Subscribe</button>
      </form>
    </body></html>
    """
    assert classify_page(html) == "article"


def test_classify_page_minimal_empty_returns_unknown():
    assert classify_page("") == "unknown"
    assert classify_page("<html><body></body></html>") == "unknown"
    assert classify_page("   ") == "unknown"


def test_classify_page_plain_page_returns_unknown():
    html = "<html><body><p>Hello world</p></body></html>"
    assert classify_page(html) == "unknown"
