"""
RSSSource — fetches articles from RSS feeds and returns full body text.

Flow:
1. Parse each configured RSS feed with feedparser
2. Score every entry: how many topic keywords appear in the title/summary?
3. Take the top-N highest-scoring entries across all feeds
4. Fetch the full article body from the entry URL via httpx
5. Return list[Article] with body populated

Fallback (--source-text):
    Pass raw text directly via ManualSource (see bottom of file).
    The pipeline uses this when the user supplies --source-text on the CLI.

Configuration:
    RSS_FEED_URLS  — comma-separated feed URLs in .env
    ASI_RSS_TOP_N  — max articles to return (default: 8)
    ASI_FETCH_TIMEOUT — httpx timeout per article fetch in seconds (default: 10)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Optional

import feedparser
import httpx

from data_sources.base_source import BaseSource
from orchestrator.job_model import Article

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FEEDS = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/business/economics/rss",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1017/rss.xml",          # NPR Business
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]

_TOP_N = int(os.environ.get("ASI_RSS_TOP_N", "8"))
_FETCH_TIMEOUT = float(os.environ.get("ASI_FETCH_TIMEOUT", "10"))
_MIN_BODY_CHARS = 100   # discard fetched pages shorter than this


# ---------------------------------------------------------------------------
# RSSSource
# ---------------------------------------------------------------------------

class RSSSource(BaseSource):
    """
    Fetches articles from RSS feeds matching a topic.

    Args:
        feed_urls: List of RSS feed URLs. Defaults to RSS_FEED_URLS env var,
                   then falls back to BBC + NYT.
        top_n:     Maximum number of articles to return.
    """

    def __init__(
        self,
        feed_urls: Optional[list[str]] = None,
        top_n: int = _TOP_N,
    ) -> None:
        if feed_urls is not None:
            self._feeds = feed_urls
        else:
            env_urls = os.environ.get("RSS_FEED_URLS", "")
            self._feeds = [u.strip() for u in env_urls.split(",") if u.strip()] or _DEFAULT_FEEDS
        self._top_n = top_n

    async def fetch(self, topic: str) -> list[Article]:
        """
        Return up to top_n articles relevant to topic, with full body text.
        Raises RuntimeError if no articles with body text are found.
        """
        keywords = [w.lower() for w in topic.split() if len(w) > 2]

        # Parse all feeds (blocking feedparser wrapped in thread executor)
        loop = asyncio.get_event_loop()
        parsed_feeds = await asyncio.gather(
            *[loop.run_in_executor(None, feedparser.parse, url) for url in self._feeds]
        )

        # Score entries by keyword hits in title + summary
        candidates: list[tuple[int, feedparser.FeedParserDict, str]] = []
        for feed in parsed_feeds:
            source_name = feed.feed.get("title", "Unknown Source")
            for entry in feed.entries:
                text = (
                    entry.get("title", "") + " " + entry.get("summary", "")
                ).lower()
                score = sum(1 for kw in keywords if kw in text)
                if score > 0:
                    candidates.append((score, entry, source_name))

        # Sort by score descending, take top_n
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[: self._top_n]

        if not top:
            raise RuntimeError(
                f"No RSS entries matched topic '{topic}' across {len(self._feeds)} feed(s)."
            )

        # Fetch full body text concurrently
        articles = await asyncio.gather(
            *[self._fetch_article(entry, source_name) for _, entry, source_name in top],
            return_exceptions=True,
        )

        # Filter out failed fetches and thin pages
        results: list[Article] = []
        for item in articles:
            if isinstance(item, Exception):
                continue
            if item is not None and len(item.body) >= _MIN_BODY_CHARS:
                results.append(item)

        if not results:
            raise RuntimeError(
                f"Fetched {len(top)} candidate articles for '{topic}' "
                f"but none returned usable body text (min {_MIN_BODY_CHARS} chars)."
            )

        return results

    async def _fetch_article(
        self, entry: feedparser.FeedParserDict, source_name: str
    ) -> Optional[Article]:
        url = entry.get("link", "")
        if not url:
            return None

        published_at: Optional[datetime] = None
        if entry.get("published_parsed"):
            import calendar
            published_at = datetime.utcfromtimestamp(
                calendar.timegm(entry.published_parsed)
            )

        body = await _fetch_body(url)
        if body is None:
            # Fall back to RSS summary if full fetch fails
            body = entry.get("summary", "")

        return Article(
            title=entry.get("title", "Untitled"),
            url=url,
            body=body,
            source_name=source_name,
            published_at=published_at,
        )


# ---------------------------------------------------------------------------
# ManualSource — used when --source-text is passed on the CLI
# ---------------------------------------------------------------------------

class ManualSource(BaseSource):
    """
    Wraps raw text supplied by the user as a single Article.
    Used as a fallback when RSS fetching is not practical.
    """

    def __init__(self, text: str, source_name: str = "Manual Input") -> None:
        self._text = text
        self._source_name = source_name

    async def fetch(self, topic: str) -> list[Article]:
        return [
            Article(
                title=topic,
                url="",
                body=self._text,
                source_name=self._source_name,
                published_at=datetime.utcnow(),
            )
        ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_body(url: str) -> Optional[str]:
    """
    Fetch the raw text content of a URL.
    Returns None on any network or HTTP error.
    Strips HTML tags with a simple approach — good enough for body extraction.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "ASI-NewsBot/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return _strip_html(response.text)
    except Exception:
        return None


def _strip_html(html: str) -> str:
    """
    Remove HTML tags and collapse whitespace.
    Not a full parser — sufficient for extracting readable article text.
    """
    import re
    # Remove script and style blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html).strip()
    return html
