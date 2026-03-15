"""
Pydantic v2 models for the Metis daily brief pipeline.

These are the central contracts. Every agent returns one of these models.
The pipeline orchestrates them. The publisher consumes them.
Never access raw LLM JSON with dict keys — always parse through these first.

Model hierarchy:
    RawStory          — one article from RSS/API (before curation)
    DailyStatus       — StatusAgent output: color + sentiment for the day
    CuratedStory      — CurationAgent output: selected + scored story
    StoryEntry        — NewsletterWriterAgent output: story + 100-150 word summary
    LayoutConfig      — LayoutAgent output: full visual identity for one edition
    RegionalEdition   — complete edition ready for HTML rendering
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Custom types
# ---------------------------------------------------------------------------

def _validate_css_color(v: str) -> str:
    """Enforce #RRGGBB hex format. Named colors and rgb() are banned."""
    if not re.match(r'^#[0-9a-fA-F]{6}$', v):
        raise ValueError(
            f"Invalid CSS color {v!r}. Must be exactly #RRGGBB (6 hex digits). "
            "Named colors (e.g. 'red') and rgb() are not allowed."
        )
    return v


# Annotated type — use as the field type for any CSS color field.
# Pydantic v2 applies the BeforeValidator before the str constraint.
from pydantic.functional_validators import BeforeValidator
CSSColor = Annotated[str, BeforeValidator(_validate_css_color)]


# ---------------------------------------------------------------------------
# StatusAgent output
# ---------------------------------------------------------------------------

class DailyStatus(BaseModel):
    """
    StatusAgent output. Describes the global mood of the day's news.
    Injected into every regional pipeline run for context.
    """

    daily_color: Literal["Red", "Amber", "Green"]
    sentiment: Literal["Tense", "Cautious", "Optimistic", "Crisis", "Volatile"]
    mood_headline: str = Field(..., max_length=200)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# News collection
# ---------------------------------------------------------------------------

class RawStory(BaseModel):
    """
    One article from an RSS feed or paid API, before any curation.
    Stored in asi2_raw_stories for audit and dedup metrics.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str
    url: str | None = None
    source_name: str
    category_hint: Literal["Politics", "Events", "Tech", "Finance"] | None = None
    body: str
    published_at: datetime | None = None


# ---------------------------------------------------------------------------
# CurationAgent output
# ---------------------------------------------------------------------------

class CuratedStory(BaseModel):
    """
    CurationAgent output: a story selected for a specific region,
    with a significance score and category assignment.
    The body is passed to NewsletterWriterAgent — it does not appear in the HTML.
    """

    raw_story_id: UUID
    title: str
    url: str | None = None
    source_name: str
    category: Literal["Politics", "Events", "Tech", "Finance"]
    significance_score: float = Field(..., ge=0.0, le=1.0)
    body: str  # full article body passed to writer


# ---------------------------------------------------------------------------
# NewsletterWriterAgent output
# ---------------------------------------------------------------------------

class StoryEntry(BaseModel):
    """
    NewsletterWriterAgent output: a story with a 100-150 word summary.
    This is the atom that goes into the HTML template.
    rank is assigned by the pipeline in significance_score order.
    """

    rank: int = Field(..., ge=1, le=8)
    category: Literal["Politics", "Events", "Tech", "Finance"]
    title: str
    url: str | None = None
    source_name: str
    summary: str = Field(..., min_length=10, max_length=1200)  # 150 words × ~8 chars/word max
    word_count: int = Field(..., ge=1)
    significance_score: float = Field(..., ge=0.0, le=1.0)
    raw_story_id: UUID

    @model_validator(mode="after")
    def word_count_must_match_summary(self) -> "StoryEntry":
        """word_count must reflect the actual word count of the summary."""
        actual = len(self.summary.split())
        if abs(actual - self.word_count) > 5:
            raise ValueError(
                f"word_count={self.word_count} does not match "
                f"actual word count of summary ({actual} words). "
                "Set word_count = len(summary.split())."
            )
        return self


# ---------------------------------------------------------------------------
# LayoutAgent output
# ---------------------------------------------------------------------------

class LayoutConfig(BaseModel):
    """
    LayoutAgent output: the full visual identity for one regional edition.

    grid_type is the no-repeat key — the pipeline enforces a 5-day rolling
    window via asi2_layout_history. If the agent returns a repeat, the pipeline
    overrides it with the least-recently-used grid_type before rendering.

    All color fields are validated as #RRGGBB hex. Named colors and rgb() raise
    a ValidationError — do not use | safe in templates, use CSS variables only.
    """

    layout_id: str           # "{region}-{date}" — informational only, always unique
    grid_type: Literal["hero-left", "hero-top", "mosaic", "timeline", "editorial"]
    primary_color: CSSColor
    secondary_color: CSSColor
    accent_color: CSSColor
    background_style: Literal["light", "dark", "warm-neutral", "cool-neutral"]
    typography_family: Literal["serif", "sans", "mixed"]
    typography_weight: Literal["light", "regular", "heavy"]
    section_order: list[Literal["Politics", "Events", "Tech", "Finance"]]
    dominant_category: Literal["Politics", "Events", "Tech", "Finance"]
    visual_weight: Literal["dense", "balanced", "airy"]
    mood_label: str
    color_rationale: str

    @field_validator("section_order")
    @classmethod
    def section_order_must_have_all_categories(
        cls, v: list[str]
    ) -> list[str]:
        expected = {"Politics", "Events", "Tech", "Finance"}
        if set(v) != expected:
            raise ValueError(
                f"section_order must contain exactly {expected}, got {set(v)}"
            )
        return v


# Safe default used when LayoutAgent fails validation after 2 retries.
# Neutral palette, most-common layout — guaranteed to pass validation.
SAFE_DEFAULT_LAYOUT = LayoutConfig(
    layout_id="default-fallback",
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
    mood_label="Neutral",
    color_rationale="Safe default — LayoutAgent validation failed after retries.",
)


# ---------------------------------------------------------------------------
# Complete regional edition
# ---------------------------------------------------------------------------

class RegionalEdition(BaseModel):
    """
    A fully assembled regional edition, ready for HTML rendering.
    Produced by the pipeline after all agents have run for one region.
    """

    region: str
    daily_status: DailyStatus
    stories: list[StoryEntry]
    layout: LayoutConfig
