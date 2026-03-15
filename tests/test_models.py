"""
P1-D1 tests — Pydantic model validation.

Tests every constraint on every model. No DB, no HTTP, no Anthropic calls.
All tests are synchronous — models are pure Python.

Coverage target: 95% of orchestrator/brief_job_model.py
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from orchestrator.brief_job_model import (
    CSSColor,
    CuratedStory,
    DailyStatus,
    LayoutConfig,
    RawStory,
    RegionalEdition,
    SAFE_DEFAULT_LAYOUT,
    StoryEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_LAYOUT_KWARGS = {
    "layout_id": "eu-2026-03-15",
    "grid_type": "hero-top",
    "primary_color": "#2c3e50",
    "secondary_color": "#ecf0f1",
    "accent_color": "#3498db",
    "background_style": "light",
    "typography_family": "sans",
    "typography_weight": "regular",
    "section_order": ["Politics", "Events", "Tech", "Finance"],
    "dominant_category": "Politics",
    "visual_weight": "balanced",
    "mood_label": "Neutral",
    "color_rationale": "Test rationale.",
}


def make_story_entry(**overrides) -> StoryEntry:
    summary = "word " * 20  # exactly 20 words
    defaults = {
        "rank": 1,
        "category": "Politics",
        "title": "Test headline",
        "url": "https://example.com/story",
        "source_name": "BBC",
        "summary": summary,
        "word_count": 20,
        "significance_score": 0.8,
        "raw_story_id": uuid.uuid4(),
    }
    return StoryEntry(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# LayoutConfig — grid_type
# ---------------------------------------------------------------------------

class TestLayoutConfigGridType:
    def test_accepts_all_valid_grid_types(self):
        valid = ["hero-left", "hero-top", "mosaic", "timeline", "editorial"]
        for grid_type in valid:
            layout = LayoutConfig(**{**VALID_LAYOUT_KWARGS, "grid_type": grid_type})
            assert layout.grid_type == grid_type

    def test_rejects_invalid_grid_type(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "grid_type": "banner"})

    def test_rejects_empty_grid_type(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "grid_type": ""})

    def test_rejects_grid_type_with_wrong_case(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "grid_type": "Hero-Top"})


# ---------------------------------------------------------------------------
# LayoutConfig — CSS color validation (_CSSColor)
# ---------------------------------------------------------------------------

class TestLayoutConfigCSSColor:
    def test_accepts_valid_lowercase_hex(self):
        layout = LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "#2c3e50"})
        assert layout.primary_color == "#2c3e50"

    def test_accepts_valid_uppercase_hex(self):
        layout = LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "#2C3E50"})
        assert layout.primary_color == "#2C3E50"

    def test_accepts_valid_mixed_case_hex(self):
        layout = LayoutConfig(**{**VALID_LAYOUT_KWARGS, "accent_color": "#aAbBcC"})
        assert layout.accent_color == "#aAbBcC"

    def test_rejects_named_css_color(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "red"})

    def test_rejects_rgb_format(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "rgb(44,62,80)"})

    def test_rejects_rgba_format(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "rgba(44,62,80,1)"})

    def test_rejects_short_hex(self):
        """#fff is valid CSS but banned — must be 6 digits."""
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "#fff"})

    def test_rejects_4digit_hex(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "#ffff"})

    def test_rejects_8digit_hex(self):
        """8-digit hex with alpha channel is not allowed."""
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "#2c3e5000"})

    def test_rejects_hex_without_hash(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "2c3e50"})

    def test_rejects_hsl_format(self):
        with pytest.raises(ValidationError, match="Invalid CSS color"):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "primary_color": "hsl(210,29%,24%)"})

    def test_all_three_color_fields_validated(self):
        """All three color fields (primary, secondary, accent) must pass the same rule."""
        for field in ("primary_color", "secondary_color", "accent_color"):
            with pytest.raises(ValidationError):
                LayoutConfig(**{**VALID_LAYOUT_KWARGS, field: "red"})


# ---------------------------------------------------------------------------
# LayoutConfig — other enum fields
# ---------------------------------------------------------------------------

class TestLayoutConfigEnums:
    def test_rejects_unknown_background_style(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "background_style": "pastel"})

    def test_rejects_unknown_typography_family(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "typography_family": "monospace"})

    def test_rejects_unknown_typography_weight(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "typography_weight": "bold"})

    def test_rejects_unknown_visual_weight(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "visual_weight": "sparse"})

    def test_rejects_unknown_dominant_category(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{**VALID_LAYOUT_KWARGS, "dominant_category": "Sports"})

    def test_section_order_must_contain_all_four_categories(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{
                **VALID_LAYOUT_KWARGS,
                "section_order": ["Politics", "Events", "Tech"],  # missing Finance
            })

    def test_section_order_rejects_duplicates(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{
                **VALID_LAYOUT_KWARGS,
                "section_order": ["Politics", "Politics", "Tech", "Finance"],
            })

    def test_section_order_rejects_unknown_category(self):
        with pytest.raises(ValidationError):
            LayoutConfig(**{
                **VALID_LAYOUT_KWARGS,
                "section_order": ["Politics", "Events", "Tech", "Sports"],
            })


# ---------------------------------------------------------------------------
# DailyStatus
# ---------------------------------------------------------------------------

class TestDailyStatus:
    def test_accepts_all_valid_colors(self):
        for color in ("Red", "Amber", "Green"):
            status = DailyStatus(
                daily_color=color,
                sentiment="Cautious",
                mood_headline="Test.",
            )
            assert status.daily_color == color

    def test_accepts_all_valid_sentiments(self):
        for s in ("Tense", "Cautious", "Optimistic", "Crisis", "Volatile"):
            status = DailyStatus(
                daily_color="Amber",
                sentiment=s,
                mood_headline="Test.",
            )
            assert status.sentiment == s

    def test_rejects_unknown_color(self):
        with pytest.raises(ValidationError):
            DailyStatus(daily_color="Purple", sentiment="Cautious", mood_headline="x")

    def test_rejects_lowercase_color(self):
        with pytest.raises(ValidationError):
            DailyStatus(daily_color="amber", sentiment="Cautious", mood_headline="x")

    def test_rejects_unknown_sentiment(self):
        with pytest.raises(ValidationError):
            DailyStatus(daily_color="Amber", sentiment="Angry", mood_headline="x")

    def test_mood_headline_max_200_chars(self):
        with pytest.raises(ValidationError):
            DailyStatus(
                daily_color="Amber",
                sentiment="Cautious",
                mood_headline="x" * 201,
            )

    def test_mood_headline_exactly_200_chars_is_ok(self):
        status = DailyStatus(
            daily_color="Amber",
            sentiment="Cautious",
            mood_headline="x" * 200,
        )
        assert len(status.mood_headline) == 200

    def test_is_frozen(self):
        """DailyStatus is immutable — assigning a field should raise."""
        status = DailyStatus(
            daily_color="Amber",
            sentiment="Cautious",
            mood_headline="Test.",
        )
        with pytest.raises(Exception):
            status.daily_color = "Red"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StoryEntry — word_count validator
# ---------------------------------------------------------------------------

class TestStoryEntry:
    def test_accepts_matching_word_count(self):
        summary = "word " * 20  # 20 words
        entry = make_story_entry(summary=summary, word_count=20)
        assert entry.word_count == 20

    def test_rejects_mismatched_word_count(self):
        summary = "word " * 20  # 20 words
        with pytest.raises(ValidationError, match="word_count"):
            make_story_entry(summary=summary, word_count=99)

    def test_allows_small_word_count_tolerance(self):
        """Off by ≤5 is tolerated (trailing whitespace, punctuation differences)."""
        summary = "word " * 20  # 20 words
        entry = make_story_entry(summary=summary, word_count=22)  # off by 2
        assert entry.word_count == 22

    def test_rejects_rank_zero(self):
        with pytest.raises(ValidationError):
            make_story_entry(rank=0)

    def test_rejects_rank_nine(self):
        with pytest.raises(ValidationError):
            make_story_entry(rank=9)

    def test_accepts_rank_one_to_eight(self):
        for rank in range(1, 9):
            entry = make_story_entry(rank=rank)
            assert entry.rank == rank

    def test_rejects_unknown_category(self):
        with pytest.raises(ValidationError):
            make_story_entry(category="Sports")

    def test_url_can_be_none(self):
        entry = make_story_entry(url=None)
        assert entry.url is None


# ---------------------------------------------------------------------------
# CuratedStory — significance_score bounds
# ---------------------------------------------------------------------------

class TestCuratedStory:
    def test_accepts_valid_significance_score(self):
        story = CuratedStory(
            raw_story_id=uuid.uuid4(),
            title="Test",
            url="https://example.com",
            source_name="BBC",
            category="Politics",
            significance_score=0.75,
            body="Body text.",
        )
        assert story.significance_score == 0.75

    def test_accepts_score_at_zero(self):
        story = CuratedStory(
            raw_story_id=uuid.uuid4(),
            title="Test",
            url=None,
            source_name="BBC",
            category="Finance",
            significance_score=0.0,
            body="Body.",
        )
        assert story.significance_score == 0.0

    def test_accepts_score_at_one(self):
        story = CuratedStory(
            raw_story_id=uuid.uuid4(),
            title="Test",
            url=None,
            source_name="BBC",
            category="Tech",
            significance_score=1.0,
            body="Body.",
        )
        assert story.significance_score == 1.0

    def test_rejects_negative_significance_score(self):
        with pytest.raises(ValidationError):
            CuratedStory(
                raw_story_id=uuid.uuid4(),
                title="Test",
                url=None,
                source_name="BBC",
                category="Politics",
                significance_score=-0.1,
                body="Body.",
            )

    def test_rejects_significance_score_above_one(self):
        with pytest.raises(ValidationError):
            CuratedStory(
                raw_story_id=uuid.uuid4(),
                title="Test",
                url=None,
                source_name="BBC",
                category="Politics",
                significance_score=1.1,
                body="Body.",
            )


# ---------------------------------------------------------------------------
# SAFE_DEFAULT_LAYOUT
# ---------------------------------------------------------------------------

class TestSafeDefaultLayout:
    def test_safe_default_is_valid_layout_config(self):
        assert isinstance(SAFE_DEFAULT_LAYOUT, LayoutConfig)

    def test_safe_default_grid_type_is_hero_top(self):
        assert SAFE_DEFAULT_LAYOUT.grid_type == "hero-top"

    def test_safe_default_colors_are_valid_hex(self):
        import re
        pattern = re.compile(r'^#[0-9a-fA-F]{6}$')
        assert pattern.match(SAFE_DEFAULT_LAYOUT.primary_color)
        assert pattern.match(SAFE_DEFAULT_LAYOUT.secondary_color)
        assert pattern.match(SAFE_DEFAULT_LAYOUT.accent_color)
