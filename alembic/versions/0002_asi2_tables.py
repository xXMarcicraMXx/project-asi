"""Metis v2 tables — asi2_* schema.

Revision ID: 0002
Revises: 001
Create Date: 2026-03-15

Creates all 5 asi2_* tables for the Metis daily regional brief pipeline.
Uses the asi2_ prefix for Oracle-safe isolation; shares the alembic_version_asi chain.

Tables:
    asi2_daily_runs      — one row per scheduler execution
    asi2_raw_stories     — all fetched stories before curation (audit trail)
    asi2_editions        — one per region per day (status, layout, publish gate)
    asi2_story_entries   — individual newsletter summaries within an edition
    asi2_layout_history  — grid_type per region per date (5-day no-repeat enforcement)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ── asi2_daily_runs ───────────────────────────────────────────────────────
    # One row per scheduler execution. The anchor for all edition and story data.
    op.create_table(
        "asi2_daily_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        # status: running | complete | partial | failed
        sa.Column("daily_color", sa.String(16), nullable=True),
        # daily_color: Red | Amber | Green
        sa.Column("sentiment", sa.String(32), nullable=True),
        # sentiment: Tense | Cautious | Optimistic | Crisis | Volatile
        sa.Column("mood_headline", sa.Text, nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("run_date", name="uq_asi2_daily_runs_run_date"),
    )

    # ── asi2_raw_stories ──────────────────────────────────────────────────────
    # All stories fetched from RSS/APIs before curation. Audit trail + dedup metrics.
    op.create_table(
        "asi2_raw_stories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asi2_daily_runs.id"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("source_name", sa.Text, nullable=True),
        sa.Column("category_hint", sa.String(32), nullable=True),
        # category_hint: Politics | Events | Tech | Finance
        sa.Column("body_preview", sa.Text, nullable=True),   # first 500 chars for audit
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_asi2_raw_stories_run", "asi2_raw_stories", ["run_id"])
    op.create_index("idx_asi2_raw_stories_url", "asi2_raw_stories", ["url"])

    # ── asi2_editions ─────────────────────────────────────────────────────────
    # One edition per region per day. Tracks status through the full pipeline.
    op.create_table(
        "asi2_editions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asi2_daily_runs.id"),
            nullable=False,
        ),
        sa.Column("region", sa.String(16), nullable=False),
        # region: eu | na | latam | apac | africa
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        # status: created | collecting | curating | writing | layout_done
        #       | pending_publish | published | cancelled | failed | no_content
        sa.Column("layout_config", postgresql.JSONB, nullable=True),
        sa.Column("html_path", sa.Text, nullable=True),
        sa.Column("publish_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancelled_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("run_id", "region", name="uq_asi2_editions_run_region"),
    )
    op.create_index(
        "idx_asi2_editions_run_region", "asi2_editions", ["run_id", "region"]
    )

    # ── asi2_story_entries ────────────────────────────────────────────────────
    # Individual newsletter summaries inside an edition. Ranked by significance.
    op.create_table(
        "asi2_story_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "edition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asi2_editions.id"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer, nullable=False),        # 1 = top story
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("source_name", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=False),        # 100-150 word newsletter summary
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("significance_score", sa.Numeric(3, 2), nullable=True),  # 0.00–1.00
        sa.Column(
            "raw_story_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asi2_raw_stories.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_asi2_story_entries_edition", "asi2_story_entries", ["edition_id", "rank"]
    )

    # ── asi2_layout_history ───────────────────────────────────────────────────
    # Tracks which grid_type was used per region per day.
    # The no-repeat key is grid_type — NOT layout_id (layout_id is always unique by date).
    # Pipeline enforces 5-day rolling window: if LayoutAgent returns a repeat,
    # the pipeline overrides with the least-recently-used grid_type from this table.
    op.create_table(
        "asi2_layout_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("grid_type", sa.String(32), nullable=False),
        # grid_type: hero-left | hero-top | mosaic | timeline | editorial
        sa.Column("layout_config_snapshot", postgresql.JSONB, nullable=True),
        sa.UniqueConstraint(
            "region", "run_date", name="uq_asi2_layout_history_region_date"
        ),
    )
    op.create_index(
        "idx_asi2_layout_history_region_date",
        "asi2_layout_history",
        ["region", "run_date"],
        postgresql_ops={"run_date": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("idx_asi2_layout_history_region_date", table_name="asi2_layout_history")
    op.drop_table("asi2_layout_history")

    op.drop_index("idx_asi2_story_entries_edition", table_name="asi2_story_entries")
    op.drop_table("asi2_story_entries")

    op.drop_index("idx_asi2_editions_run_region", table_name="asi2_editions")
    op.drop_table("asi2_editions")

    op.drop_index("idx_asi2_raw_stories_url", table_name="asi2_raw_stories")
    op.drop_index("idx_asi2_raw_stories_run", table_name="asi2_raw_stories")
    op.drop_table("asi2_raw_stories")

    op.drop_table("asi2_daily_runs")
