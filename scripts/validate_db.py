"""
Day 1 validation script — confirms asyncpg connects and all 6 tables exist.
Run on VPS after applying db/schema.sql:

    python scripts/validate_db.py
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

EXPECTED_TABLES = {
    "jobs",
    "briefs",
    "content_pieces",
    "agent_runs",
    "feedback_loops",
    "layout_templates",
}


async def validate() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)

    print(f"Connecting to: {url.split('@')[-1]}")  # hide credentials in output

    engine = create_async_engine(url, poolclass=NullPool)

    async with engine.connect() as conn:
        # Verify connection
        result = await conn.execute(text("SELECT version()"))
        version = result.scalar()
        print(f"PostgreSQL: {version}")

        # Check all expected tables exist
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' "
                "ORDER BY tablename"
            )
        )
        existing = {row[0] for row in result.fetchall()}

    await engine.dispose()

    missing = EXPECTED_TABLES - existing
    extra = existing - EXPECTED_TABLES

    if missing:
        print(f"\nFAIL — missing tables: {sorted(missing)}")
        sys.exit(1)

    print(f"\nAll {len(EXPECTED_TABLES)} tables present: {sorted(EXPECTED_TABLES)}")
    if extra:
        print(f"Note: extra tables found (not from ASI): {sorted(extra)}")
    print("\nDay 1 DB validation PASSED.")


if __name__ == "__main__":
    asyncio.run(validate())
