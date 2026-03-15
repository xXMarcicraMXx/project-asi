"""
P1-D1 Validation Script — DB schema + Pydantic models

Asserts:
  1. All 5 asi2_* tables exist in the database
  2. All required indexes exist
  3. asi2_layout_history has grid_type column (not layout_id)
  4. Alembic is at head (alembic_version_asi = '0002')
  5. INSERT + SELECT round-trip on each table
  6. Pydantic models import and validate correctly
  7. Downgrade (-1) and re-upgrade work cleanly

Usage:
    DATABASE_URL=postgresql+asyncpg://... python scripts/validate_p1d1.py

Exit code 0 = all assertions passed.
Exit code 1 = failure (see output for details).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import date

# Make repo root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

errors: list[str] = []


def ok(msg: str) -> None:
    print(f"  {PASS} {msg}")


def fail(msg: str) -> None:
    print(f"  {FAIL} {msg}")
    errors.append(msg)


# ---------------------------------------------------------------------------
# DB assertions
# ---------------------------------------------------------------------------

REQUIRED_TABLES = [
    "asi2_daily_runs",
    "asi2_raw_stories",
    "asi2_editions",
    "asi2_story_entries",
    "asi2_layout_history",
]

REQUIRED_INDEXES = [
    "idx_asi2_raw_stories_run",
    "idx_asi2_raw_stories_url",
    "idx_asi2_editions_run_region",
    "idx_asi2_story_entries_edition",
    "idx_asi2_layout_history_region_date",
]


async def check_tables(conn) -> None:
    print("\n[1] Tables")
    result = await conn.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename LIKE 'asi2_%'"
        )
    )
    existing = {row[0] for row in result.fetchall()}
    for table in REQUIRED_TABLES:
        if table in existing:
            ok(table)
        else:
            fail(f"{table} NOT FOUND")


async def check_indexes(conn) -> None:
    print("\n[2] Indexes")
    result = await conn.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname LIKE 'idx_asi2_%'"
        )
    )
    existing = {row[0] for row in result.fetchall()}
    for idx in REQUIRED_INDEXES:
        if idx in existing:
            ok(idx)
        else:
            fail(f"{idx} NOT FOUND")


async def check_grid_type_column(conn) -> None:
    print("\n[3] asi2_layout_history.grid_type column (not layout_id)")
    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'asi2_layout_history' AND column_name = 'grid_type'"
        )
    )
    if result.fetchone():
        ok("grid_type column exists")
    else:
        fail("grid_type column MISSING from asi2_layout_history")

    result = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'asi2_layout_history' AND column_name = 'layout_id'"
        )
    )
    if result.fetchone():
        fail("layout_id column should NOT exist in asi2_layout_history (wrong no-repeat key)")
    else:
        ok("layout_id column correctly absent")


async def check_alembic_version(conn) -> None:
    print("\n[4] Alembic version")
    try:
        result = await conn.execute(
            text("SELECT version_num FROM alembic_version_asi")
        )
        row = result.fetchone()
        if row and row[0] == "0002":
            ok(f"alembic_version_asi = {row[0]}")
        elif row:
            fail(f"alembic_version_asi = {row[0]} (expected '0002')")
        else:
            fail("alembic_version_asi table is empty")
    except Exception as e:
        fail(f"Could not read alembic_version_asi: {e}")


async def check_round_trips(conn) -> None:
    print("\n[5] INSERT + SELECT round-trips")

    run_id = uuid.uuid4()
    story_id = uuid.uuid4()
    edition_id = uuid.uuid4()

    # asi2_daily_runs
    await conn.execute(
        text(
            "INSERT INTO asi2_daily_runs (id, run_date, status) "
            "VALUES (:id, :d, 'running')"
        ),
        {"id": str(run_id), "d": date.today()},
    )
    row = await conn.execute(
        text("SELECT id FROM asi2_daily_runs WHERE id = :id"), {"id": str(run_id)}
    )
    if row.fetchone():
        ok("asi2_daily_runs INSERT + SELECT")
    else:
        fail("asi2_daily_runs round-trip failed")

    # asi2_raw_stories
    await conn.execute(
        text(
            "INSERT INTO asi2_raw_stories (id, run_id, title) "
            "VALUES (:id, :run_id, 'Test story')"
        ),
        {"id": str(story_id), "run_id": str(run_id)},
    )
    row = await conn.execute(
        text("SELECT id FROM asi2_raw_stories WHERE id = :id"), {"id": str(story_id)}
    )
    if row.fetchone():
        ok("asi2_raw_stories INSERT + SELECT")
    else:
        fail("asi2_raw_stories round-trip failed")

    # asi2_editions
    await conn.execute(
        text(
            "INSERT INTO asi2_editions (id, run_id, region, status) "
            "VALUES (:id, :run_id, 'eu', 'created')"
        ),
        {"id": str(edition_id), "run_id": str(run_id)},
    )
    row = await conn.execute(
        text("SELECT id FROM asi2_editions WHERE id = :id"), {"id": str(edition_id)}
    )
    if row.fetchone():
        ok("asi2_editions INSERT + SELECT")
    else:
        fail("asi2_editions round-trip failed")

    # asi2_story_entries
    entry_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO asi2_story_entries "
            "(id, edition_id, rank, category, title, summary) "
            "VALUES (:id, :eid, 1, 'Politics', 'Test', 'Test summary text here.')"
        ),
        {"id": str(entry_id), "eid": str(edition_id)},
    )
    row = await conn.execute(
        text("SELECT id FROM asi2_story_entries WHERE id = :id"), {"id": str(entry_id)}
    )
    if row.fetchone():
        ok("asi2_story_entries INSERT + SELECT")
    else:
        fail("asi2_story_entries round-trip failed")

    # asi2_layout_history
    hist_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO asi2_layout_history (id, region, run_date, grid_type) "
            "VALUES (:id, 'eu', :d, 'hero-top')"
        ),
        {"id": str(hist_id), "d": date.today()},
    )
    row = await conn.execute(
        text("SELECT id FROM asi2_layout_history WHERE id = :id"), {"id": str(hist_id)}
    )
    if row.fetchone():
        ok("asi2_layout_history INSERT + SELECT")
    else:
        fail("asi2_layout_history round-trip failed")

    # Roll back all test data
    await conn.execute(
        text("DELETE FROM asi2_story_entries WHERE id = :id"), {"id": str(entry_id)}
    )
    await conn.execute(
        text("DELETE FROM asi2_editions WHERE id = :id"), {"id": str(edition_id)}
    )
    await conn.execute(
        text("DELETE FROM asi2_raw_stories WHERE id = :id"), {"id": str(story_id)}
    )
    await conn.execute(
        text("DELETE FROM asi2_layout_history WHERE id = :id"), {"id": str(hist_id)}
    )
    await conn.execute(
        text("DELETE FROM asi2_daily_runs WHERE id = :id"), {"id": str(run_id)}
    )


# ---------------------------------------------------------------------------
# Pydantic model assertions
# ---------------------------------------------------------------------------

def check_pydantic_models() -> None:
    print("\n[6] Pydantic models")
    from pydantic import ValidationError

    try:
        from orchestrator.brief_job_model import (
            DailyStatus,
            RawStory,
            CuratedStory,
            StoryEntry,
            LayoutConfig,
            RegionalEdition,
            SAFE_DEFAULT_LAYOUT,
            CSSColor,
        )
        ok("All models import successfully")
    except ImportError as e:
        fail(f"Import error: {e}")
        return

    # DailyStatus valid
    try:
        DailyStatus(daily_color="Amber", sentiment="Cautious", mood_headline="Test")
        ok("DailyStatus accepts valid values")
    except Exception as e:
        fail(f"DailyStatus valid case failed: {e}")

    # DailyStatus rejects bad color
    try:
        DailyStatus(daily_color="Purple", sentiment="Cautious", mood_headline="x")
        fail("DailyStatus should reject 'Purple'")
    except ValidationError:
        ok("DailyStatus rejects unknown color 'Purple'")

    # DailyStatus rejects bad sentiment
    try:
        DailyStatus(daily_color="Amber", sentiment="Angry", mood_headline="x")
        fail("DailyStatus should reject 'Angry'")
    except ValidationError:
        ok("DailyStatus rejects unknown sentiment 'Angry'")

    # DailyStatus mood_headline max length
    try:
        DailyStatus(daily_color="Amber", sentiment="Cautious", mood_headline="x" * 201)
        fail("DailyStatus should reject mood_headline > 200 chars")
    except ValidationError:
        ok("DailyStatus rejects mood_headline > 200 chars")

    # LayoutConfig valid
    valid_layout = {
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
        "color_rationale": "Test",
    }
    try:
        LayoutConfig(**valid_layout)
        ok("LayoutConfig accepts valid values")
    except Exception as e:
        fail(f"LayoutConfig valid case failed: {e}")

    # CSSColor rejects named color
    try:
        LayoutConfig(**{**valid_layout, "primary_color": "red"})
        fail("LayoutConfig should reject named color 'red'")
    except ValidationError:
        ok("LayoutConfig rejects named CSS color 'red'")

    # CSSColor rejects rgb()
    try:
        LayoutConfig(**{**valid_layout, "primary_color": "rgb(1,2,3)"})
        fail("LayoutConfig should reject 'rgb(1,2,3)'")
    except ValidationError:
        ok("LayoutConfig rejects rgb() color format")

    # CSSColor rejects short hex
    try:
        LayoutConfig(**{**valid_layout, "primary_color": "#fff"})
        fail("LayoutConfig should reject short hex '#fff'")
    except ValidationError:
        ok("LayoutConfig rejects short hex '#fff'")

    # CSSColor accepts uppercase
    try:
        LayoutConfig(**{**valid_layout, "primary_color": "#2C3E50"})
        ok("LayoutConfig accepts uppercase hex '#2C3E50'")
    except Exception as e:
        fail(f"LayoutConfig should accept uppercase hex: {e}")

    # LayoutConfig rejects unknown grid_type
    try:
        LayoutConfig(**{**valid_layout, "grid_type": "banner"})
        fail("LayoutConfig should reject unknown grid_type 'banner'")
    except ValidationError:
        ok("LayoutConfig rejects unknown grid_type 'banner'")

    # SAFE_DEFAULT_LAYOUT is valid
    try:
        assert SAFE_DEFAULT_LAYOUT.grid_type == "hero-top"
        ok("SAFE_DEFAULT_LAYOUT is valid and grid_type='hero-top'")
    except Exception as e:
        fail(f"SAFE_DEFAULT_LAYOUT invalid: {e}")

    # StoryEntry word_count validator
    try:
        import uuid as _uuid
        StoryEntry(
            rank=1,
            category="Politics",
            title="Test",
            url=None,
            source_name="BBC",
            summary="word " * 20,   # 20 words
            word_count=20,
            significance_score=0.8,
            raw_story_id=_uuid.uuid4(),
        )
        ok("StoryEntry accepts matching word_count")
    except Exception as e:
        fail(f"StoryEntry valid case failed: {e}")

    try:
        import uuid as _uuid
        StoryEntry(
            rank=1,
            category="Politics",
            title="Test",
            url=None,
            source_name="BBC",
            summary="word " * 20,   # 20 words
            word_count=99,           # wrong
            significance_score=0.8,
            raw_story_id=_uuid.uuid4(),
        )
        fail("StoryEntry should reject mismatched word_count")
    except ValidationError:
        ok("StoryEntry rejects mismatched word_count")


# ---------------------------------------------------------------------------
# Alembic downgrade / re-upgrade
# ---------------------------------------------------------------------------

def check_downgrade_upgrade() -> None:
    print("\n[7] Alembic downgrade -1 then upgrade head")
    db_url = os.environ.get("DATABASE_URL", "")
    # Convert asyncpg URL to sync for alembic CLI
    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    env = {**os.environ, "DATABASE_URL": sync_url}

    r = subprocess.run(
        ["alembic", "downgrade", "-1"],
        capture_output=True, text=True, env=env
    )
    if r.returncode == 0:
        ok("alembic downgrade -1 succeeded")
    else:
        fail(f"alembic downgrade -1 failed: {r.stderr.strip()}")
        return

    r = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True, env=env
    )
    if r.returncode == 0:
        ok("alembic upgrade head succeeded after downgrade")
    else:
        fail(f"alembic upgrade head failed: {r.stderr.strip()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)

    engine = create_async_engine(db_url, echo=False)

    print("=" * 60)
    print("P1-D1 Validation — Metis v2 DB schema + Pydantic models")
    print("=" * 60)

    async with engine.connect() as conn:
        await check_tables(conn)
        await check_indexes(conn)
        await check_grid_type_column(conn)
        await check_alembic_version(conn)
        await check_round_trips(conn)
        await conn.commit()

    await engine.dispose()

    check_pydantic_models()
    check_downgrade_upgrade()

    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED — P1-D1 complete ✓")


if __name__ == "__main__":
    asyncio.run(main())
