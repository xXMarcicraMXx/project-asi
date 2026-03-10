"""Initial schema — baseline migration.

Revision ID: 001
Revises:
Create Date: 2026-03-09

This migration captures the full schema as of Sprint 3 Day 16.
On a fresh database it creates all tables.
On the existing VPS database, run:

    alembic stamp 001

to mark it as already applied without re-running the DDL.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ── jobs ─────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project", sa.String(32), nullable=False, server_default="asi"),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("regions", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("status", sa.String(32), server_default="running"),
        sa.Column("config_snapshot", postgresql.JSONB),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_jobs_project", "jobs", ["project"])

    # ── briefs ────────────────────────────────────────────────────────────────
    op.create_table(
        "briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id")),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), server_default="draft"),
        sa.Column("layout_config", postgresql.JSONB, nullable=True),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # ── content_pieces ────────────────────────────────────────────────────────
    op.create_table(
        "content_pieces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("briefs.id")),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("headline", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("iteration_count", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(32), server_default="draft"),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # ── agent_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_piece_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_pieces.id"),
        ),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("iteration", sa.Integer, server_default="1"),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # ── feedback_loops ────────────────────────────────────────────────────────
    op.create_table(
        "feedback_loops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_piece_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_pieces.id"),
        ),
        sa.Column("iteration", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("feedback", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # ── layout_templates ──────────────────────────────────────────────────────
    op.create_table(
        "layout_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("config_schema", postgresql.JSONB, nullable=True),
        sa.Column("preview_url", sa.Text, nullable=True),
        sa.Column("active", sa.Boolean, server_default="true"),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("layout_templates")
    op.drop_table("feedback_loops")
    op.drop_table("agent_runs")
    op.drop_table("content_pieces")
    op.drop_table("briefs")
    op.drop_index("idx_jobs_project", table_name="jobs")
    op.drop_table("jobs")
