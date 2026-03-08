"""
Shared Pydantic v2 models — the central contracts for the entire ASI pipeline.
Every agent, orchestrator, and DB writer depends on these types.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    running = "running"
    complete = "complete"
    failed = "failed"


class ContentPieceStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    human_review = "human_review"
    published = "published"


class EditorDecision(str, Enum):
    approve = "approve"
    revise = "revise"


# ---------------------------------------------------------------------------
# Data source models
# ---------------------------------------------------------------------------

class Article(BaseModel):
    """A single source article fetched from an RSS feed or provided manually."""

    title: str
    url: str
    body: str
    source_name: str
    published_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Pipeline input models
# ---------------------------------------------------------------------------

class JobPayload(BaseModel):
    """
    The top-level input that triggers a full pipeline run.
    Created by the CLI or the scheduler and passed to the orchestrator.
    """

    id: UUID = Field(default_factory=uuid4)
    topic: str = Field(..., min_length=3, max_length=512)
    content_type: str = Field(default="journal_article")
    regions: list[str] = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegionTask(BaseModel):
    """
    A single unit of work: produce one content piece for one region.
    Derived from a JobPayload by the orchestrator.
    """

    job_id: UUID
    region_id: str          # e.g. "EU", "LATAM", "SEA", "NA"
    content_type: str
    topic: str


# ---------------------------------------------------------------------------
# Agent output models
# ---------------------------------------------------------------------------

class ResearchBrief(BaseModel):
    """
    Structured output from ResearchAgent.
    Contains extracted facts, quotes, and data points — not free text.
    """

    topic: str
    key_facts: list[str] = Field(..., min_length=1)
    direct_quotes: list[str] = Field(default_factory=list)
    data_points: list[str] = Field(default_factory=list)
    conflicting_perspectives: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class ArticleDraft(BaseModel):
    """
    Output from WriterAgent — a complete markdown article draft.
    """

    headline: str = Field(..., min_length=5)
    body: str = Field(..., min_length=100)
    word_count: int = Field(..., gt=0)
    region_id: str
    iteration: int = Field(default=1, ge=1)


class EditorVerdict(BaseModel):
    """
    Structured JSON output from EditorAgent.
    Strict schema — the agent is prompted to return exactly this shape.
    """

    status: EditorDecision
    feedback: str = Field(..., min_length=1)
