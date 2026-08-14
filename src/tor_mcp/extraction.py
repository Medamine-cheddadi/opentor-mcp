"""Content extraction utilities — HTML to markdown, forum thread/post parsing."""

from __future__ import annotations

import logging
import re
from typing import TypedDict

from bs4 import BeautifulSoup

logger = logging.getLogger("tor-mcp.extraction")

# ── Quality scoring ────────────────────────────────────────────

# Thresholds for quality classification
_GOOD_THRESHOLD = 0.6
_FAIR_THRESHOLD = 0.3


class ExtractionResult(TypedDict):
    """Result produced by the extraction fallback chain."""

    content: str
    quality: str  # "good" | "fair" | "poor"
    strategy: str  # which strategy produced this result
    quality_warning: str | None  # set when quality is "poor"


def score_quality(text: str, html: str = "") -> float:
    """Score extracted text quality on a 0.0–1.0 scale.

    Heuristic based on:
    - Character count (longer meaningful text scores higher)
    - Structural element count (headings, lists, links in the text)
    - Content-to-boilerplate ratio (script/style bytes vs total HTML bytes)
    """
    if not text or not text.strip():
        return 0.0

    stripped = text.strip()

    # --- Character count component (0.0–0.4) ---
    char_count = len(stripped)
    if char_count > 500:
        char_score = 0.4
    elif char_count > 100:
        char_score = 0.2 + 0.2 * (char_count - 100) / 400
    elif char_count > 20:
        char_score = 0.05 + 0.15 * (char_count - 20) / 80
    else:
        char_score = 0.05 * char_count / 20

    # --- Structural element count component (0.0–0.3) ---
    heading_count = len(re.findall(r"^#{1,6}\s", stripped, re.MULTILINE))
    list_count = len(re.findall(r"^[\s]*[-*+]\s", stripped, re.MULTILINE))
    link_count = len(re.findall(r"\[.*?\]\(.*?\)", stripped))
    structure_total = heading_count + list_count + link_count
    if structure_total >= 5:
        structure_score = 0.3
    elif structure_total >= 2:
        structure_score = 0.15 + 0.15 * (structure_total - 2) / 3
    elif structure_total >= 1:
        structure_score = 0.1
    else:
        structure_score = 0.0

    # --- Content-to-boilerplate ratio component (0.0–0.3) ---
    if html:
        soup = BeautifulSoup(html, "html.parser")
        boilerplate_len = sum(
            len(tag.get_text()) for tag in soup.find_all(["script", "style"])
        )
        total_text_len = len(soup.get_text())
        if total_text_len > 0:
            content_ratio = 1.0 - (boilerplate_len / total_text_len)
            ratio_score = 0.3 * max(0.0, content_ratio)
        else:
            ratio_score = 0.0
    else:
        # Without HTML context, give partial credit
        ratio_score = 0.15

    return min(1.0, char_score + structure_score + ratio_score)


def _classify_quality(score: float) -> str:
    """Map a numeric score to a quality label."""
    if score >= _GOOD_THRESHOLD:
        return "good"
    if score >= _FAIR_THRESHOLD:
        return "fair"
    return "poor"


# ── Extraction strategies ──────────────────────────────────────


def _markdownify_strategy(html: str) -> str:
    """Primary strategy: convert HTML to markdown using markdownify."""
    try:
        from markdownify import markdownify as md

        result = md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav"],
        )
        # Clean up excessive whitespace
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()
    except ImportError:
        return ""


def _beautifulsoup_strategy(html: str) -> str:
    """Secondary strategy: structured text extraction via BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove boilerplate
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    parts: list[str] = []

    # Extract headings with markdown formatting
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        level = int(heading.name[1])
        text = heading.get_text(strip=True)
        if text:
            parts.append(f"{'#' * level} {text}")

    # Extract images with alt text
    for img in soup.find_all("img"):
        alt = str(img.get("alt", "")).strip()
        src = str(img.get("src", "")).strip()
        if alt:
            parts.append(f"![{alt}]({src})" if src else f"[Image: {alt}]")

    # Extract tables
    for table in soup.find_all("table"):
        table_md = _table_to_markdown(table)
        if table_md:
            parts.append(table_md)

    # Extract lists
    for list_tag in soup.find_all(["ul", "ol"], recursive=True):
        # Skip nested lists — they are handled by their parent
        if list_tag.find_parent(["ul", "ol"]):
            continue
        list_md = _list_to_markdown(list_tag, indent=0)
        if list_md:
            parts.append(list_md)

    # Extract remaining paragraph text
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text and len(text) > 5:
            parts.append(text)

    return "\n\n".join(parts).strip()


def _raw_text_strategy(html: str) -> str:
    """Terminal strategy: plain text extraction."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _table_to_markdown(table) -> str:
    """Convert an HTML table element to markdown table format."""
    rows = table.find_all("tr")
    if not rows:
        return ""

    md_rows: list[str] = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        cell_texts = [cell.get_text(strip=True).replace("|", "\\|") for cell in cells]
        if cell_texts:
            md_rows.append("| " + " | ".join(cell_texts) + " |")
            # Add separator after header row (first row with <th>)
            if row.find("th") and len(md_rows) == 1:
                md_rows.append("| " + " | ".join("---" for _ in cell_texts) + " |")

    # If no header separator was added and we have rows, add one after the first row
    if len(md_rows) >= 1 and not any("---" in r for r in md_rows):
        first_row = md_rows[0]
        col_count = first_row.count("|") - 1
        if col_count > 0:
            md_rows.insert(1, "| " + " | ".join("---" for _ in range(col_count)) + " |")

    return "\n".join(md_rows)


def _list_to_markdown(list_tag, indent: int = 0) -> str:
    """Convert an HTML list element to markdown with proper nesting."""
    lines: list[str] = []
    is_ordered = list_tag.name == "ol"
    counter = 1

    for li in list_tag.find_all("li", recursive=False):
        # Get direct text content (not nested list text)
        text_parts = []
        for child in li.children:
            if hasattr(child, "name") and child.name in ("ul", "ol"):
                continue  # Skip nested lists — handled below
            if hasattr(child, "get_text"):
                t = child.get_text(strip=True)
            else:
                t = str(child).strip()
            if t:
                text_parts.append(t)

        text = " ".join(text_parts)
        prefix = "  " * indent
        if is_ordered:
            lines.append(f"{prefix}{counter}. {text}")
            counter += 1
        else:
            lines.append(f"{prefix}- {text}")

        # Process nested lists
        for nested in li.find_all(["ul", "ol"], recursive=False):
            nested_md = _list_to_markdown(nested, indent=indent + 1)
            if nested_md:
                lines.append(nested_md)

    return "\n".join(lines)


# ── Fallback chain ─────────────────────────────────────────────

_STRATEGIES = [
    ("markdownify", _markdownify_strategy),
    ("beautifulsoup", _beautifulsoup_strategy),
    ("raw_text", _raw_text_strategy),
]


def extract_content(html: str) -> ExtractionResult:
    """Extract content from HTML using a fallback chain with quality scoring.

    Tries strategies in order: markdownify → BeautifulSoup structured → raw text.
    Returns the first result that scores above the "good" threshold, or the
    best result found across all strategies with a quality warning if none
    are good enough.

    Never raises — always returns best-effort content with quality metadata.
    """
    best: tuple[float, str, str] | None = None  # (score, content, strategy_name)

    for name, strategy in _STRATEGIES:
        try:
            result = strategy(html)
        except Exception:
            logger.debug("Strategy %s failed, continuing", name, exc_info=True)
            continue

        score = score_quality(result, html)
        logger.debug("Strategy %s scored %.2f (%d chars)", name, score, len(result))

        if score >= _GOOD_THRESHOLD:
            return ExtractionResult(
                content=result,
                quality="good",
                strategy=name,
                quality_warning=None,
            )

        if best is None or score > best[0]:
            best = (score, result, name)

    # All strategies exhausted — return the best we found
    if best is not None:
        quality = _classify_quality(best[0])
        warning = (
            "Extraction produced low-quality output. Consider retrying with "
            "a different wait_strategy or after the page fully loads."
            if quality == "poor"
            else None
        )
        return ExtractionResult(
            content=best[1],
            quality=quality,
            strategy=best[2],
            quality_warning=warning,
        )

    # Nothing at all — empty input or all strategies crashed
    return ExtractionResult(
        content="",
        quality="poor",
        strategy="none",
        quality_warning="All extraction strategies produced empty output.",
    )


# ── Legacy interface (backward-compatible) ─────────────────────


def html_to_markdown(html: str, base_url: str = "") -> str:
    """Convert HTML to clean readable markdown.

    Preserved for backward compatibility.  New callers should prefer
    ``extract_content()`` which adds quality metadata.
    """
    result = _markdownify_strategy(html)
    if result:
        return result
    # Fallback when markdownify is not installed
    return _raw_text_strategy(html)


def extract_forum_threads(html: str) -> list[dict]:
    """Extract forum thread listings from common forum HTML patterns.

    Works with common forum engines: phpBB, XenForo, Dread, simple HTML forums.
    Uses heuristics to find thread-like structures.
    """
    soup = BeautifulSoup(html, "html.parser")
    threads = []

    # Strategy 1: Look for common thread list patterns
    # phpBB / XenForo style
    for row in soup.select(
        ".threadlist .thread, .discussionListItem, .topic-list tr, "
        "tr.topic, .thread-row, li.thread, .post-listing .post, "
        ".structItem, .node-body"
    ):
        thread = _extract_thread_from_element(row)
        if thread:
            threads.append(thread)

    # Strategy 2: Dread-style (Reddit-like)
    if not threads:
        for post in soup.select(
            ".post, .submission, .thing, article, .entry, .list-group-item, .card"
        ):
            thread = _extract_thread_from_element(post)
            if thread:
                threads.append(thread)

    # Strategy 3: Generic — find repeated structures with links
    if not threads:
        threads = _extract_threads_generic(soup)

    return threads


def _extract_thread_from_element(element) -> dict | None:
    """Extract thread info from a single HTML element."""
    # Find the main link (title)
    link = element.find("a")
    if not link:
        return None

    title = link.get_text(strip=True)
    if not title or len(title) < 3:
        return None

    href = link.get("href", "")

    # Try to find author
    author_el = element.select_one(
        ".author, .username, .user, .by, .poster, "
        "[class*='author'], [class*='user'], [class*='poster']"
    )
    author = author_el.get_text(strip=True) if author_el else None

    # Try to find date
    date_el = element.select_one(
        "time, .date, .timestamp, .time, .age, [class*='date'], [class*='time'], [datetime]"
    )
    date = None
    if date_el:
        date = date_el.get("datetime") or date_el.get("title") or date_el.get_text(strip=True)

    # Try to find reply/comment count
    count_el = element.select_one(
        ".replies, .comments, .count, .posts, [class*='repl'], [class*='comment'], [class*='count']"
    )
    replies = count_el.get_text(strip=True) if count_el else None

    # Try to find preview/snippet
    snippet_el = element.select_one(
        ".preview, .snippet, .summary, .description, .teaser, p, .text, [class*='preview']"
    )
    snippet = None
    if snippet_el and snippet_el != link:
        snippet = snippet_el.get_text(strip=True)[:300]

    return {
        "title": title,
        "url": href,
        "author": author,
        "date": date,
        "replies": replies,
        "snippet": snippet,
    }


def _extract_threads_generic(soup: BeautifulSoup) -> list[dict]:
    """Fallback: find repeated link structures that look like thread listings."""
    threads = []
    # Look for lists or repeated containers
    for container in soup.select("ul, ol, table tbody, div[class], section"):
        links = container.find_all("a", href=True)
        if len(links) >= 3:  # At least 3 links to look like a listing
            for link in links:
                text = link.get_text(strip=True)
                if text and len(text) > 10:  # Skip navigation links
                    threads.append(
                        {
                            "title": text[:200],
                            "url": link["href"],
                            "author": None,
                            "date": None,
                            "replies": None,
                            "snippet": None,
                        }
                    )
    return threads


def extract_forum_posts(html: str) -> list[dict]:
    """Extract individual posts from a forum thread page."""
    soup = BeautifulSoup(html, "html.parser")
    posts = []

    # Find post containers
    post_elements = soup.select(
        ".post, .message, .comment, .reply, article, "
        ".postbody, .messageContent, .post-body, "
        ".bbp-reply-content, .entry-content, "
        "[class*='post'], [class*='comment'], [class*='reply']"
    )

    for el in post_elements:
        post = _extract_post_from_element(el)
        if post:
            posts.append(post)

    # Fallback: if no structured posts found, return the whole page text
    if not posts:
        text = soup.get_text(separator="\n", strip=True)
        if text:
            posts.append(
                {
                    "author": None,
                    "date": None,
                    "content": text[:5000],
                    "index": 0,
                }
            )

    return posts


def _extract_post_from_element(element) -> dict | None:
    """Extract a single post's content."""
    # Get the text content
    # Remove nested quotes first to get clean content
    content_el = element.__copy__() if hasattr(element, "__copy__") else element
    content = content_el.get_text(separator="\n", strip=True)

    if not content or len(content) < 5:
        return None

    # Find author
    author_el = element.select_one(
        ".author, .username, .user, .poster, .name, "
        "[class*='author'], [class*='user'], [class*='poster']"
    )
    author = author_el.get_text(strip=True) if author_el else None

    # Find date
    date_el = element.select_one(
        "time, .date, .timestamp, .time, .age, [class*='date'], [class*='time'], [datetime]"
    )
    date = None
    if date_el:
        date = date_el.get("datetime") or date_el.get("title") or date_el.get_text(strip=True)

    return {
        "author": author,
        "date": date,
        "content": content[:5000],
    }


def extract_metadata(html: str) -> dict:
    """Extract page metadata (title, description, keywords, etc.)."""
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else None

    def meta(name: str) -> str | None:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        content = tag.get("content") if tag else None
        return str(content).strip() if content else None

    canonical = soup.find("link", rel="canonical")
    return {
        "title": title,
        "description": meta("description") or meta("og:description"),
        "keywords": meta("keywords"),
        "author": meta("author"),
        "og_title": meta("og:title"),
        "og_image": meta("og:image"),
        "canonical": canonical["href"] if canonical else None,
    }


# ── Pagination detection ─────────────────────────────────────

_NEXT_LINK_PATTERNS = re.compile(
    r"^(next|next\s*page|next\s*→|→|»|›|older|more|forward)$",
    re.IGNORECASE,
)


def detect_pagination(html: str) -> str | None:
    """Detect a 'next page' link and return its URL, or ``None``."""
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    link_next = soup.find("link", rel="next", href=True)
    if link_next:
        return str(link_next["href"])

    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(strip=True)
        if text and _NEXT_LINK_PATTERNS.fullmatch(text):
            return str(anchor["href"])

    a_rel_next = soup.find("a", rel="next", href=True)
    if a_rel_next:
        return str(a_rel_next["href"])

    return None


# ── Content-type classification ───────────────────────────────


def classify_page(html: str) -> str:
    """Classify a page by content type using local heuristic HTML analysis.

    Returns one of: ``"article"``, ``"forum"``, ``"search_results"``,
    ``"login_form"``, ``"directory_listing"``, ``"error_page"``, ``"unknown"``.

    Each heuristic returns a confidence score (0.0–1.0).  The category with the
    highest score wins, provided it exceeds a minimum threshold.  When no
    category is confident enough the function returns ``"unknown"``.
    """
    if not html or not html.strip():
        return "unknown"

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup

    scores: dict[str, float] = {
        "login_form": _score_login_form(body),
        "error_page": _score_error_page(body, html),
        "search_results": _score_search_results(body),
        "forum": _score_forum(body),
        "article": _score_article(body),
        "directory_listing": _score_directory_listing(body),
    }

    best_type = max(scores, key=scores.__getitem__)
    if scores[best_type] < 0.3:
        return "unknown"
    return best_type


# ── Per-type scoring heuristics ───────────────────────────────


def _score_login_form(body) -> float:
    """<form> with password field is a strong login signal."""
    score = 0.0
    forms = body.find_all("form")
    if not forms:
        return 0.0
    for form in forms:
        password_inputs = form.find_all("input", attrs={"type": "password"})
        if password_inputs:
            score = 0.7
            # Boost if there's also a text/email input (username field)
            text_inputs = form.find_all(
                "input", attrs={"type": re.compile(r"^(text|email)$")}
            )
            if text_inputs:
                score = 0.9
            break
    return score


def _score_error_page(body, html: str) -> float:
    """HTTP error status codes + error keywords in title/headings."""
    score = 0.0
    title_tag = body.find("title") or (body.parent.find("title") if body.parent else None)
    title_text = title_tag.get_text(strip=True).lower() if title_tag else ""

    headings = body.find_all(re.compile(r"^h[1-3]$"))
    heading_text = " ".join(h.get_text(strip=True).lower() for h in headings)

    error_patterns = re.compile(
        r"(404|403|500|502|503)\b|"
        r"\b(not found|forbidden|internal server error|bad gateway|"
        r"service unavailable|access denied|page not found|error occurred)\b"
    )

    if error_patterns.search(title_text):
        score += 0.5
    if error_patterns.search(heading_text):
        score += 0.4

    # Short page with error signal is very likely an error page
    all_text = body.get_text(strip=True)
    if len(all_text) < 500 and score > 0:
        score += 0.2

    return min(1.0, score)


def _score_search_results(body) -> float:
    """Search form + repeated result items."""
    score = 0.0

    # Search form present
    search_inputs = body.find_all(
        "input", attrs={"type": re.compile(r"^(search|text)$")}
    )
    search_forms = [
        inp for inp in search_inputs
        if inp.find_parent("form") is not None
    ]
    if search_forms:
        score += 0.2

    # Look for search-result-like repeated structures
    result_selectors = (
        ".result, .search-result, .search-results li, "
        ".search-results > div, [class*='result'], [class*='search'] li"
    )
    results = body.select(result_selectors)
    if len(results) >= 3:
        score += 0.5
    elif len(results) >= 1:
        score += 0.2

    # Check for pagination (common in search)
    pagination = body.select(".pagination, .pager, nav[aria-label*='page'], [class*='pagina']")
    if pagination:
        score += 0.1

    return min(1.0, score)


def _score_forum(body) -> float:
    """Repeated .post/.thread/.comment containers with author/date metadata."""
    score = 0.0

    # Direct forum CSS class matches
    forum_selectors = (
        ".thread, .post, .comment, .reply, .topic, "
        ".threadlist, .discussionListItem, .structItem, "
        "[class*='thread'], [class*='post'], [class*='comment'], "
        "[class*='forum'], [class*='topic']"
    )
    forum_elements = body.select(forum_selectors)

    if len(forum_elements) >= 5:
        score += 0.5
    elif len(forum_elements) >= 2:
        score += 0.3

    # Author/date metadata near forum elements
    author_els = body.select(
        ".author, .username, .user, .poster, "
        "[class*='author'], [class*='user'], [class*='poster']"
    )
    date_els = body.select(
        "time, .date, .timestamp, [class*='date'], [class*='time']"
    )
    if author_els and date_els:
        score += 0.2
    elif author_els or date_els:
        score += 0.1

    # Reply/comment counts
    reply_els = body.select(
        ".replies, .comments, .count, [class*='repl'], [class*='comment-count']"
    )
    if reply_els:
        score += 0.1

    return min(1.0, score)


def _score_article(body) -> float:
    """<article> tags or large text blocks with headings."""
    score = 0.0

    # <article> tag is a strong signal
    articles = body.find_all("article")
    if articles:
        score += 0.3

    # Long text content
    all_text = body.get_text(strip=True)
    if len(all_text) > 2000:
        score += 0.2
    elif len(all_text) > 500:
        score += 0.1

    # Multiple headings suggest structured article content
    headings = body.find_all(re.compile(r"^h[1-6]$"))
    if len(headings) >= 3:
        score += 0.2
    elif len(headings) >= 1:
        score += 0.1

    # Paragraphs with substantial text
    paragraphs = body.find_all("p")
    long_paragraphs = [p for p in paragraphs if len(p.get_text(strip=True)) > 100]
    if len(long_paragraphs) >= 3:
        score += 0.2
    elif len(long_paragraphs) >= 1:
        score += 0.1

    return min(1.0, score)


def _score_directory_listing(body) -> float:
    """Repeated link lists with uniform structure."""
    score = 0.0

    # Look for lists containing mostly links
    for list_tag in body.find_all(["ul", "ol"]):
        items = list_tag.find_all("li", recursive=False)
        if len(items) < 5:
            continue
        items_with_links = [li for li in items if li.find("a", href=True)]
        if len(items_with_links) >= len(items) * 0.7:
            score = max(score, 0.5)
            # Uniform structure bonus: all items have similar child count
            child_counts = [len(list(li.children)) for li in items]
            if child_counts and max(child_counts) - min(child_counts) <= 2:
                score += 0.2

    # File-listing patterns (common in directory indexes)
    file_links = body.find_all("a", href=re.compile(r"\.\w{1,5}$"))
    if len(file_links) >= 5:
        score += 0.2

    # Table-based directory listing
    tables = body.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) >= 5:
            rows_with_links = [r for r in rows if r.find("a", href=True)]
            if len(rows_with_links) >= len(rows) * 0.7:
                score = max(score, 0.5)

    return min(1.0, score)
