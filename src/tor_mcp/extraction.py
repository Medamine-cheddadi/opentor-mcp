"""Content extraction utilities — HTML to markdown, forum thread/post parsing."""

import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger("tor-mcp.extraction")


def html_to_markdown(html: str, base_url: str = "") -> str:
    """Convert HTML to clean readable markdown."""
    try:
        from markdownify import markdownify as md

        result = md(html, heading_style="ATX", bullets="-", strip=["script", "style", "nav"])
        # Clean up excessive whitespace
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()
    except ImportError:
        # Fallback: just extract text
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)


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
