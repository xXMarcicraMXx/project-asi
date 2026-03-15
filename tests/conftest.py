"""
Shared pytest fixtures for Metis v2 tests.

All external I/O is mocked here:
  - Anthropic API calls → mock_anthropic_response fixture
  - HTTP/RSS feeds      → respx-based mocking
  - File system         → pytest tmp_path (built-in)
  - DB                  → in-memory SQLite via aiosqlite (schema-compatible subset)

Usage in tests:
    def test_something(sample_raw_stories, sample_layout_config):
        ...
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.brief_job_model import (
    CuratedStory,
    DailyStatus,
    LayoutConfig,
    RawStory,
    StoryEntry,
)


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_daily_status() -> DailyStatus:
    return DailyStatus(
        daily_color="Amber",
        sentiment="Cautious",
        mood_headline="Global markets remain uncertain amid conflicting signals.",
    )


@pytest.fixture
def sample_raw_stories() -> list[RawStory]:
    """10 varied raw stories across all categories."""
    categories = ["Politics", "Events", "Tech", "Finance"]
    stories = []
    for i in range(10):
        stories.append(
            RawStory(
                id=uuid.uuid4(),
                title=f"Story {i + 1}: Test headline for category {categories[i % 4]}",
                url=f"https://example.com/story-{i + 1}",
                source_name="Test Source",
                category_hint=categories[i % 4],
                body=f"This is the body of story {i + 1}. " * 20,
                published_at=datetime(2026, 3, 15, 12, 0, 0),
            )
        )
    return stories


@pytest.fixture
def sample_curated_stories(sample_raw_stories) -> list[CuratedStory]:
    """6 curated stories derived from sample_raw_stories."""
    categories = ["Politics", "Events", "Tech", "Finance", "Politics", "Tech"]
    return [
        CuratedStory(
            raw_story_id=sample_raw_stories[i].id,
            title=sample_raw_stories[i].title,
            url=sample_raw_stories[i].url,
            source_name=sample_raw_stories[i].source_name,
            category=categories[i],
            significance_score=round(0.9 - i * 0.05, 2),
            body=sample_raw_stories[i].body,
        )
        for i in range(6)
    ]


@pytest.fixture
def sample_story_entries(sample_curated_stories) -> list[StoryEntry]:
    """6 story entries with valid 100-150 word summaries."""
    entries = []
    for i, curated in enumerate(sample_curated_stories):
        # Build a summary that is between 100-150 words
        summary = (
            f"This is the newsletter summary for story {i + 1}. "
            "It covers the key developments in the region. "
            "The situation has evolved significantly over the past week. "
            "Analysts are watching closely as events unfold. "
            "The impact on local populations remains a primary concern. "
            "Officials have issued statements addressing the public directly. "
            "Further developments are expected in the coming days. "
            "This story has significant implications for the broader region. "
            "Stakeholders continue to monitor the situation carefully. "
            "The outcome will shape policy decisions for months to come."
        )
        word_count = len(summary.split())
        entries.append(
            StoryEntry(
                rank=i + 1,
                category=curated.category,
                title=curated.title,
                url=curated.url,
                source_name=curated.source_name,
                summary=summary,
                word_count=word_count,
                significance_score=curated.significance_score,
                raw_story_id=curated.raw_story_id,
            )
        )
    return entries


@pytest.fixture
def sample_layout_config() -> LayoutConfig:
    return LayoutConfig(
        layout_id="eu-2026-03-15",
        grid_type="hero-top",
        primary_color="#2c3e50",
        secondary_color="#ecf0f1",
        accent_color="#3498db",
        background_style="light",
        typography_family="sans",
        typography_weight="regular",
        section_order=["Politics", "Events", "Tech", "Finance"],
        dominant_category="Politics",
        visual_weight="balanced",
        mood_label="Cautious",
        color_rationale="Muted blues for a cautious day.",
    )


# ---------------------------------------------------------------------------
# Mock Anthropic client
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic_response():
    """
    Factory fixture. Call it with a string to get a mock that returns that
    string as the LLM response content.

    Usage:
        def test_something(mock_anthropic_response):
            mock = mock_anthropic_response('{"daily_color": "Amber", ...}')
            with patch("anthropic.AsyncAnthropic", return_value=mock):
                ...
    """
    def _make_mock(content: str):
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=content)]
        mock_message.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        return mock_client

    return _make_mock


# ---------------------------------------------------------------------------
# Temporary site directory
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_site_dir(tmp_path):
    """
    Creates a site/{eu,na,latam,apac,africa}/ structure under pytest's tmp_path.
    Used by HtmlPublisher tests.
    """
    regions = ["eu", "na", "latam", "apac", "africa"]
    for region in regions:
        region_dir = tmp_path / "site" / region
        region_dir.mkdir(parents=True, exist_ok=True)
        last_good = region_dir / "last-good"
        last_good.mkdir(exist_ok=True)
    return tmp_path / "site"
