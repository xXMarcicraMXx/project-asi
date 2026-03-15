"""
SQLAlchemy ORM models for Metis v2 (asi2_* tables).

Uses a separate Base and MetaData from db/models.py so that Alembic autogenerate
never accidentally sees or modifies the legacy ASI / Oracle shared tables.
The isolation boundary is the asi2_ prefix enforced at the table level.

DO NOT import Base from this module into alembic/env.py — migrations for these
tables are written explicitly in alembic/versions/0002_asi2_tables.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class BaseV2(DeclarativeBase):
    """Isolated base for all asi2_* Metis models."""
    pass


class Asi2DailyRun(BaseV2):
    """
    One row per scheduler execution.
    Anchor table — all editions and raw stories reference this.

    Status flow: running → complete | partial | failed
    """

    __tablename__ = "asi2_daily_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_date = Column(Date, nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="running")
    # running | complete | partial | failed
    daily_color = Column(String(16), nullable=True)
    # Red | Amber | Green — set by StatusAgent
    sentiment = Column(String(32), nullable=True)
    # Tense | Cautious | Optimistic | Crisis | Volatile
    mood_headline = Column(Text, nullable=True)
    total_cost_usd = Column(Numeric(10, 6), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    raw_stories = relationship("Asi2RawStory", back_populates="run")
    editions = relationship("Asi2Edition", back_populates="run")


class Asi2RawStory(BaseV2):
    """
    Every story fetched from RSS/APIs before curation.
    Kept for audit trail and deduplication metrics (P1-D2 logs URLs that
    appear in ≥2 region pools).
    """

    __tablename__ = "asi2_raw_stories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("asi2_daily_runs.id"), nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    source_name = Column(Text, nullable=True)
    category_hint = Column(String(32), nullable=True)
    # Politics | Events | Tech | Finance — keyword-inferred, not agent-assigned
    body_preview = Column(Text, nullable=True)               # first 500 chars
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    run = relationship("Asi2DailyRun", back_populates="raw_stories")
    story_entries = relationship("Asi2StoryEntry", back_populates="raw_story")


class Asi2Edition(BaseV2):
    """
    One edition per region per day. Tracks the complete lifecycle from
    curation through publish (or cancel).

    Status machine:
        created → collecting → curating → writing → layout_done
             → pending_publish → published
                              → cancelled
        (any step) → failed
        (curation) → no_content
    """

    __tablename__ = "asi2_editions"
    __table_args__ = (
        UniqueConstraint("run_id", "region", name="uq_asi2_editions_run_region"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("asi2_daily_runs.id"), nullable=False)
    region = Column(String(16), nullable=False)
    # eu | na | latam | apac | africa
    status = Column(String(32), nullable=False, default="created")
    layout_config = Column(JSONB, nullable=True)             # full LayoutConfig JSON
    html_path = Column(Text, nullable=True)                  # /var/www/metis/{region}/index.html
    publish_at = Column(TIMESTAMP(timezone=True), nullable=True)    # set when cancel gate opens
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    cancelled_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    run = relationship("Asi2DailyRun", back_populates="editions")
    story_entries = relationship("Asi2StoryEntry", back_populates="edition")


class Asi2StoryEntry(BaseV2):
    """
    Individual newsletter summary inside an edition.
    Ranked by significance_score descending (rank=1 is the top story).
    """

    __tablename__ = "asi2_story_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edition_id = Column(UUID(as_uuid=True), ForeignKey("asi2_editions.id"), nullable=False)
    rank = Column(Integer, nullable=False)                   # 1–8
    category = Column(String(32), nullable=False)            # Politics | Events | Tech | Finance
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    source_name = Column(Text, nullable=True)
    summary = Column(Text, nullable=False)                   # 100-150 word newsletter summary
    word_count = Column(Integer, nullable=True)
    significance_score = Column(Numeric(3, 2), nullable=True)  # 0.00–1.00
    raw_story_id = Column(
        UUID(as_uuid=True), ForeignKey("asi2_raw_stories.id"), nullable=True
    )
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    edition = relationship("Asi2Edition", back_populates="story_entries")
    raw_story = relationship("Asi2RawStory", back_populates="story_entries")


class Asi2LayoutHistory(BaseV2):
    """
    Records which grid_type was used per region per day.

    CRITICAL: The no-repeat key is grid_type, NOT layout_id.
    layout_id is "{region}-{date}" — always unique, enforcing no-repeat on it does nothing.
    The 5-day rolling window check queries this table by (region, run_date DESC LIMIT 5).
    If LayoutAgent returns a grid_type already in the last 5 rows for this region,
    the pipeline overrides it with the least-recently-used grid_type.
    """

    __tablename__ = "asi2_layout_history"
    __table_args__ = (
        UniqueConstraint(
            "region", "run_date", name="uq_asi2_layout_history_region_date"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region = Column(String(16), nullable=False)
    run_date = Column(Date, nullable=False)
    grid_type = Column(String(32), nullable=False)
    # hero-left | hero-top | mosaic | timeline | editorial
    layout_config_snapshot = Column(JSONB, nullable=True)   # full config for debugging
