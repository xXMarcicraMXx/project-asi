"""
SQLAlchemy async ORM models — mirrors the raw SQL schema exactly.
All tables use UUID PKs and TIMESTAMPTZ for created_at fields.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    ARRAY,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Job(Base):
    """A single pipeline run."""

    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic = Column(Text, nullable=False)
    content_type = Column(String(64), nullable=False)
    regions = Column(ARRAY(Text), nullable=False)
    status = Column(String(32), default="running")          # running | complete | failed
    config_snapshot = Column(JSONB)                          # full YAML config at run time
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    briefs = relationship("Brief", back_populates="job")


class Brief(Base):
    """
    The publishable unit.
    MVP: one brief per region per job.
    Evolution 1: one brief per job (the full daily brief).
    """

    __tablename__ = "briefs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    title = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    status = Column(String(32), default="draft")             # draft | approved | rejected | human_review | published
    layout_config = Column(JSONB, nullable=True)             # Evolution 2: agent-generated layout spec
    approved_by = Column(String(128), nullable=True)
    published_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    job = relationship("Job", back_populates="briefs")
    content_pieces = relationship("ContentPiece", back_populates="brief")


class ContentPiece(Base):
    """
    The content atom — one regional perspective.
    MVP: one piece per brief (the article).
    Evolution 1: N pieces per brief (regional sections of the daily brief).
    """

    __tablename__ = "content_pieces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brief_id = Column(UUID(as_uuid=True), ForeignKey("briefs.id"))
    region = Column(String(64), nullable=False)
    content_type = Column(String(64), nullable=False)        # 'regional_article' | 'brief_section'
    headline = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=True)
    iteration_count = Column(Integer, default=0)
    status = Column(String(32), default="draft")
    extra = Column("metadata", JSONB, nullable=True)         # flexible: source_urls, tags, etc.
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    brief = relationship("Brief", back_populates="content_pieces")
    agent_runs = relationship("AgentRun", back_populates="content_piece")
    feedback_loops = relationship("FeedbackLoop", back_populates="content_piece")


class AgentRun(Base):
    """Full audit trail of every agent invocation."""

    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_piece_id = Column(UUID(as_uuid=True), ForeignKey("content_pieces.id"))
    agent_name = Column(String(64), nullable=False)
    iteration = Column(Integer, default=1)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    content_piece = relationship("ContentPiece", back_populates="agent_runs")


class FeedbackLoop(Base):
    """Editor feedback per revision cycle."""

    __tablename__ = "feedback_loops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_piece_id = Column(UUID(as_uuid=True), ForeignKey("content_pieces.id"))
    iteration = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)              # 'approve' | 'revise'
    feedback = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    content_piece = relationship("ContentPiece", back_populates="feedback_loops")


class LayoutTemplate(Base):
    """
    Layout template library.
    Created now — used in Evolution 2 by the LayoutAgent.
    """

    __tablename__ = "layout_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(Text, nullable=True)                # natural language, readable by LayoutAgent
    config_schema = Column(JSONB, nullable=True)             # valid parameters and allowed values
    preview_url = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
