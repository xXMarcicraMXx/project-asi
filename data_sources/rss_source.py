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
# MetisRSSCollector — Metis v2 news collection (region-aware, no topic filter)
# ---------------------------------------------------------------------------

import logging as _logging
from pathlib import Path as _Path

import yaml as _yaml

from orchestrator.brief_job_model import RawStory as _RawStory

_mclog = _logging.getLogger(__name__)

# Keyword sets for category hinting.
# Not authoritative — CurationAgent assigns the final category.
# Matched case-insensitively against title + RSS summary.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Politics": [
        "election", "president", "parliament", "government", "minister",
        "senate", "congress", "vote", "treaty", "diplomat", "sanctions",
        "legislation", "constitutional", "political", "prime minister",
        "foreign policy", "nato", "un security", "referendum",
    ],
    "Finance": [
        "bank", "market", "stock", "gdp", "inflation", "interest rate",
        "federal reserve", "ecb", "economy", "trade deficit", "tariff",
        "currency", "bond", "investment", "fiscal", "monetary", "imf",
        "central bank", "recession", "earnings", "ipo",
    ],
    "Tech": [
        "artificial intelligence", " ai ", "technology", "software", "cyber",
        "semiconductor", "chip", "data breach", "digital", "internet",
        "startup", "machine learning", "cloud computing", "quantum",
        "big tech", "regulation tech", "open source",
    ],
    "Events": [
        "conflict", "war", "airstrike", "disaster", "earthquake", "flood",
        "protest", "attack", "summit", "crisis", "hurricane", "wildfire",
        "explosion", "coup", "riot", "ceasefire", "humanitarian", "killed",
        "victims", "evacuation",
    ],
}

_NEWS_SOURCES_YAML = (
    _Path(__file__).parent.parent / "config" / "news_sources.yaml"
)


def _hint_category(title: str, summary: str) -> str | None:
    """
    Return the most likely category based on keyword matches in title + summary.
    Returns None if no category scores above zero (CurationAgent will assign it).
    """
    text = (title + " " + summary).lower()
    scores: dict[str, int] = {cat: 0 for cat in _CATEGORY_KEYWORDS}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[cat] += 1
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else None


def log_duplicate_urls(stories_by_region: dict[str, list[_RawStory]]) -> None:
    """
    Log URLs that appear in ≥2 region story pools.
    Call this after collecting all 5 regions — used for Phase 4 dedup metrics.

    Args:
        stories_by_region: mapping of region_id → list of RawStory
    """
    url_regions: dict[str, list[str]] = {}
    for region, stories in stories_by_region.items():
        for story in stories:
            if story.url:
                url_regions.setdefault(story.url, []).append(region)

    duplicates = {url: regions for url, regions in url_regions.items() if len(regions) >= 2}
    if duplicates:
        _mclog.info(
            "cross_region_duplicates",
            extra={
                "count": len(duplicates),
                "urls": [
                    {"url": url, "regions": regions}
                    for url, regions in list(duplicates.items())[:20]  # cap log size
                ],
            },
        )
    else:
        _mclog.debug("cross_region_duplicates", extra={"count": 0})


class MetisRSSCollector:
    """
    Region-aware RSS collector for the Metis daily brief pipeline.

    Loads feed URLs from config/news_sources.yaml — global feeds are merged
    with the region-specific feeds. Returns list[RawStory] (Metis v2 type,
    not the v1 Article type used by the Oracle pipeline).

    Error handling:
      - Single feed timeout (httpx.TimeoutException): log WARNING, skip, continue
      - Single feed HTTP error: log WARNING with status code, skip, continue
      - All feeds return 0 stories: raise RuntimeError (pipeline sends Slack alert)

    Category hinting:
      Keyword-based pre-classification (Politics/Events/Tech/Finance).
      Not authoritative — CurationAgent assigns the final category.
      If a feed entry has a category_hint in news_sources.yaml, that takes
      precedence over the keyword classifier.

    Optional env toggles (both default off):
      NEWSAPI_ENABLED=true   NEWSAPI_KEY=...
      GDELT_ENABLED=true
    """

    def __init__(
        self,
        region_id: str,
        config_path: _Path | None = None,
    ) -> None:
        self._region_id = region_id.lower()
        self._config_path = config_path or _NEWS_SOURCES_YAML
        self._feeds: list[dict] = []  # populated by _load_feeds()

    def _load_feeds(self) -> list[dict]:
        """Load and merge global + regional feeds from YAML."""
        with open(self._config_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f)

        global_feeds: list[dict] = cfg.get("global", [])
        regional_feeds: list[dict] = cfg.get("regions", {}).get(self._region_id, [])

        if not regional_feeds:
            _mclog.warning(
                "no_regional_feeds",
                extra={"region": self._region_id, "config": str(self._config_path)},
            )

        return global_feeds + regional_feeds

    async def collect(self) -> list[_RawStory]:
        """
        Fetch all feeds and return a deduplicated list of RawStory objects.

        Raises:
            RuntimeError: if all feeds fail or return 0 parseable entries.
        """
        self._feeds = self._load_feeds()

        loop = asyncio.get_event_loop()

        # Parse all feeds concurrently via thread executor (feedparser is sync)
        tasks = [
            loop.run_in_executor(None, feedparser.parse, feed["url"])
            for feed in self._feeds
        ]
        parsed = await asyncio.gather(*tasks, return_exceptions=True)

        stories: list[_RawStory] = []
        seen_urls: set[str] = set()
        feeds_ok = 0

        for feed_cfg, result in zip(self._feeds, parsed):
            url = feed_cfg["url"]
            yaml_hint: str | None = feed_cfg.get("category_hint")
            source_name: str = feed_cfg.get("name", url)

            if isinstance(result, Exception):
                _mclog.warning(
                    "feed_parse_error",
                    extra={"feed_url": url, "error": str(result)},
                )
                continue

            if result.bozo and not result.entries:
                _mclog.warning(
                    "feed_parse_empty",
                    extra={"feed_url": url, "bozo_exception": str(result.get("bozo_exception", ""))},
                )
                continue

            feeds_ok += 1
            for entry in result.entries:
                entry_url: str = entry.get("link", "")

                # Dedup within this collection run
                if entry_url and entry_url in seen_urls:
                    continue
                if entry_url:
                    seen_urls.add(entry_url)

                title: str = entry.get("title", "").strip()
                if not title:
                    continue

                summary: str = entry.get("summary", "") or entry.get("description", "")

                # Category hint: YAML config > keyword classifier
                category_hint = yaml_hint or _hint_category(title, summary)

                published_at: datetime | None = None
                if entry.get("published_parsed"):
                    import calendar
                    try:
                        published_at = datetime.utcfromtimestamp(
                            calendar.timegm(entry.published_parsed)
                        )
                    except Exception:
                        pass

                # Use RSS summary as body — avoids 50+ HTTP fetches per run.
                # The WriterAgent receives this as context for its 100-150 word summary.
                body = summary or title

                stories.append(
                    _RawStory(
                        title=title,
                        url=entry_url or None,
                        source_name=source_name,
                        category_hint=category_hint,  # type: ignore[arg-type]
                        body=body,
                        published_at=published_at,
                        # body_preview truncated to 500 chars for DB audit column
                    )
                )

        _mclog.info(
            "collection_complete",
            extra={
                "region": self._region_id,
                "feeds_attempted": len(self._feeds),
                "feeds_ok": feeds_ok,
                "stories": len(stories),
            },
        )

        if not stories:
            raise RuntimeError(
                f"Metis news collection returned 0 stories for region '{self._region_id}'. "
                f"Attempted {len(self._feeds)} feeds, {feeds_ok} parsed successfully. "
                "Pipeline halted — check feed registry and network connectivity."
            )

        return stories


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
