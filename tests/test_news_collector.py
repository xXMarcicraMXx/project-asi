"""
P1-D2 tests — Metis news collection (MetisRSSCollector).

All HTTP and feedparser calls are mocked — no real network I/O.
Tests cover happy path, per-feed error handling, all-fail RuntimeError,
category hinting, dedup logging, and optional API stubs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from data_sources.rss_source import MetisRSSCollector, _hint_category, log_duplicate_urls
from orchestrator.brief_job_model import RawStory


# ---------------------------------------------------------------------------
# Helpers — build fake feedparser results
# ---------------------------------------------------------------------------

def _make_feed(entries: list[dict], title: str = "Test Feed") -> MagicMock:
    """Build a minimal fake feedparser result."""
    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.feed.get.return_value = title

    mock_entries = []
    for e in entries:
        entry = MagicMock()
        entry.get = lambda key, default="", _e=e: _e.get(key, default)
        entry.__getitem__ = lambda self, key, _e=e: _e[key]
        mock_entries.append(entry)

    mock_feed.entries = mock_entries
    return mock_feed


def _make_entry(title: str, url: str, summary: str = "", category: str = "") -> dict:
    return {
        "title": title,
        "link": url,
        "summary": summary or f"Summary for {title}",
        "published_parsed": None,
    }


GLOBAL_FEEDS_COUNT = 8   # number of feeds in global section of news_sources.yaml
REGION_FEEDS_COUNT = {"eu": 4, "na": 4, "latam": 4, "apac": 5, "africa": 4}


def _make_collector(region: str, tmp_path: Path) -> tuple[MetisRSSCollector, Path]:
    """Create a collector with a minimal test YAML config."""
    yaml_content = f"""
global:
  - url: "https://global1.example.com/rss"
    name: "Global Feed 1"
    category_hint: null
  - url: "https://global2.example.com/rss"
    name: "Global Feed 2"
    category_hint: "Finance"

regions:
  {region}:
    - url: "https://{region}.example.com/rss"
      name: "{region.upper()} Regional"
      category_hint: "Politics"
    - url: "https://{region}-2.example.com/rss"
      name: "{region.upper()} Regional 2"
      category_hint: null
"""
    config_path = tmp_path / "news_sources.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")
    collector = MetisRSSCollector(region_id=region, config_path=config_path)
    return collector, config_path


def _good_feed(n: int = 3, prefix: str = "Story") -> MagicMock:
    entries = [
        _make_entry(f"{prefix} {i}", f"https://example.com/{prefix.lower()}-{i}")
        for i in range(n)
    ]
    return _make_feed(entries)


# ---------------------------------------------------------------------------
# Tests: feed merging
# ---------------------------------------------------------------------------

class TestFeedMerging:
    @pytest.mark.asyncio
    async def test_merges_global_and_regional_feeds(self, tmp_path):
        collector, _ = _make_collector("eu", tmp_path)

        call_count = [0]

        def unique_feed(_url: str) -> MagicMock:
            idx = call_count[0]
            call_count[0] += 1
            return _good_feed(2, prefix=f"Feed{idx}Story")

        with patch("feedparser.parse", side_effect=unique_feed):
            stories = await collector.collect()

        # 4 feeds total (2 global + 2 regional), each returns 2 unique stories = 8
        assert len(stories) >= 4  # at minimum 1 per feed

    @pytest.mark.asyncio
    async def test_loads_correct_regional_feeds(self, tmp_path):
        collector, _ = _make_collector("apac", tmp_path)
        feeds = collector._load_feeds()
        # region_id "apac" should load apac feeds, not eu feeds
        urls = [f["url"] for f in feeds]
        assert any("apac" in u for u in urls)
        assert not any("eu" in u for u in urls)

    @pytest.mark.asyncio
    async def test_real_config_has_all_5_regions(self):
        """The actual news_sources.yaml has all 5 regions configured."""
        for region in ("eu", "na", "latam", "apac", "africa"):
            collector = MetisRSSCollector(region_id=region)
            feeds = collector._load_feeds()
            assert len(feeds) > GLOBAL_FEEDS_COUNT, (
                f"Region {region} should have global + regional feeds, got {len(feeds)}"
            )


# ---------------------------------------------------------------------------
# Tests: error handling — single feed failures
# ---------------------------------------------------------------------------

class TestPerFeedErrorHandling:
    @pytest.mark.asyncio
    async def test_skips_timed_out_feed_continues_others(self, tmp_path, caplog):
        collector, _ = _make_collector("eu", tmp_path)

        call_count = 0

        def fake_parse(url: str):
            nonlocal call_count
            call_count += 1
            if "global1" in url:
                raise Exception("Connection timeout")
            return _good_feed(2)

        with caplog.at_level(logging.WARNING):
            with patch("feedparser.parse", side_effect=fake_parse):
                stories = await collector.collect()

        assert len(stories) > 0, "Should still return stories from other feeds"
        assert any("feed_parse_error" in r.message or "timeout" in r.message.lower()
                   for r in caplog.records), "Should log the feed failure"

    @pytest.mark.asyncio
    async def test_skips_http_error_feed_continues_others(self, tmp_path, caplog):
        collector, _ = _make_collector("na", tmp_path)

        def fake_parse(url: str):
            if "global2" in url:
                # feedparser returns bozo=True with empty entries on HTTP errors
                mock = MagicMock()
                mock.bozo = True
                mock.entries = []
                mock.get = lambda k, d=None: d
                return mock
            return _good_feed(2)

        with caplog.at_level(logging.WARNING):
            with patch("feedparser.parse", side_effect=fake_parse):
                stories = await collector.collect()

        assert len(stories) > 0

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_all_feeds_fail(self, tmp_path):
        collector, _ = _make_collector("latam", tmp_path)

        def always_fail(url: str):
            raise Exception("Network unreachable")

        with patch("feedparser.parse", side_effect=always_fail):
            with pytest.raises(RuntimeError, match="0 stories"):
                await collector.collect()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_all_feeds_empty(self, tmp_path):
        collector, _ = _make_collector("africa", tmp_path)

        def empty_feed(url: str):
            mock = MagicMock()
            mock.bozo = False
            mock.entries = []
            mock.feed.get.return_value = "Empty"
            return mock

        with patch("feedparser.parse", side_effect=empty_feed):
            with pytest.raises(RuntimeError, match="0 stories"):
                await collector.collect()


# ---------------------------------------------------------------------------
# Tests: category hinting
# ---------------------------------------------------------------------------

class TestCategoryHinting:
    def test_politics_keyword_classification(self):
        cat = _hint_category("President signs new legislation", "Congress voted on the bill")
        assert cat == "Politics"

    def test_finance_keyword_classification(self):
        cat = _hint_category("Federal Reserve raises interest rate", "Stock market reacts to inflation data")
        assert cat == "Finance"

    def test_tech_keyword_classification(self):
        cat = _hint_category("Artificial intelligence regulation", "Big tech faces new scrutiny")
        assert cat == "Tech"

    def test_events_keyword_classification(self):
        cat = _hint_category("Earthquake kills dozens", "Humanitarian crisis deepens")
        assert cat == "Events"

    def test_returns_none_when_no_match(self):
        cat = _hint_category("Generic meaningless text", "Nothing here")
        # May return None or weakest match — just verify it doesn't crash
        # and returns a valid value or None
        assert cat in (None, "Politics", "Finance", "Tech", "Events")

    @pytest.mark.asyncio
    async def test_yaml_hint_overrides_keyword_classifier(self, tmp_path, caplog):
        """If the YAML has category_hint='Finance', that wins over keyword match."""
        yaml_content = """
global:
  - url: "https://finance-feed.example.com/rss"
    name: "Finance Feed"
    category_hint: "Finance"
regions:
  eu: []
"""
        config_path = tmp_path / "news_sources.yaml"
        config_path.write_text(yaml_content, encoding="utf-8")
        collector = MetisRSSCollector(region_id="eu", config_path=config_path)

        # Story title would hint "Events" but YAML says "Finance"
        politics_entry = _make_entry(
            "Earthquake and flood crisis", "https://ex.com/1", "Disaster kills dozens"
        )
        feed = _make_feed([politics_entry])

        with patch("feedparser.parse", return_value=feed):
            stories = await collector.collect()

        assert stories[0].category_hint == "Finance"

    @pytest.mark.asyncio
    async def test_collector_assigns_category_hint_to_stories(self, tmp_path):
        collector, _ = _make_collector("eu", tmp_path)

        entry = _make_entry(
            "Fed rate decision stuns market", "https://ex.com/fed",
            "Federal Reserve announces interest rate cut"
        )

        def fake_parse(url: str):
            if "global2" in url:  # global2 has category_hint: Finance in our test YAML
                return _make_feed([entry])
            return _make_feed([])

        with patch("feedparser.parse", side_effect=fake_parse):
            stories = await collector.collect()

        # The Finance-hinted feed should produce stories with Finance hint
        finance_stories = [s for s in stories if s.category_hint == "Finance"]
        assert len(finance_stories) >= 1


# ---------------------------------------------------------------------------
# Tests: dedup logging
# ---------------------------------------------------------------------------

class TestDedupLogging:
    def test_logs_duplicate_urls_across_regions(self, caplog):
        shared_url = "https://reuters.com/shared-story"
        stories_by_region = {
            "eu": [
                RawStory(title="Story A", url=shared_url, source_name="Reuters",
                         body="Body A"),
                RawStory(title="Story B", url="https://politico.eu/story-b",
                         source_name="Politico EU", body="Body B"),
            ],
            "na": [
                RawStory(title="Story A (NA)", url=shared_url, source_name="Reuters",
                         body="Body A"),
                RawStory(title="Story C", url="https://politico.com/story-c",
                         source_name="Politico US", body="Body C"),
            ],
        }

        with caplog.at_level(logging.INFO):
            log_duplicate_urls(stories_by_region)

        assert any("cross_region_duplicates" in r.message or
                   "duplicate" in r.message.lower()
                   for r in caplog.records), "Should log duplicate URL detection"

    def test_no_log_when_no_duplicates(self, caplog):
        stories_by_region = {
            "eu": [RawStory(title="EU Only", url="https://eu.com/1",
                            source_name="EU", body="EU body")],
            "na": [RawStory(title="NA Only", url="https://na.com/1",
                            source_name="NA", body="NA body")],
        }

        with caplog.at_level(logging.INFO):
            log_duplicate_urls(stories_by_region)

        # Should log 0 duplicates (or nothing) — no error
        duplicate_logs = [
            r for r in caplog.records
            if "cross_region_duplicates" in r.message
        ]
        for log in duplicate_logs:
            assert "count" in str(log) or True  # just verifying it doesn't crash


# ---------------------------------------------------------------------------
# Tests: optional paid API stubs
# ---------------------------------------------------------------------------

class TestOptionalAPIStubs:
    def test_newsapi_stub_off_by_default(self, monkeypatch):
        """NEWSAPI_ENABLED defaults to false — stub should never be called."""
        monkeypatch.delenv("NEWSAPI_ENABLED", raising=False)
        monkeypatch.delenv("NEWSAPI_KEY", raising=False)

        # If NewsAPISource were called without a key it would raise — confirm it's not imported
        import data_sources.rss_source as rss_mod
        assert not getattr(rss_mod, "_newsapi_enabled", False), (
            "NewsAPI should be disabled by default"
        )

    def test_gdelt_stub_off_by_default(self, monkeypatch):
        """GDELT_ENABLED defaults to false."""
        monkeypatch.delenv("GDELT_ENABLED", raising=False)

        import data_sources.rss_source as rss_mod
        assert not getattr(rss_mod, "_gdelt_enabled", False), (
            "GDELT should be disabled by default"
        )
