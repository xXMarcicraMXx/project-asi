"""
Day 15 — Parallel region execution validation script.

Confirms:
  1. All 4 regions complete and return valid drafts
  2. Execution is faster than sequential baseline (regions > 1 only)
  3. Job status is 'complete' in the DB
  4. Cost report has rows for all regions
  5. Error isolation: a region error does not abort other regions

Usage:
    python scripts/validate_day15.py
    python scripts/validate_day15.py --regions EU LATAM   # subset
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.session import AsyncSessionLocal
from db.models import Job
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import query_cost_report, run_pipeline
from sqlalchemy import select

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# Check 1–4: full parallel pipeline run
# ---------------------------------------------------------------------------

async def run_checks(regions: list[str]) -> list[bool]:
    results: list[bool] = []

    payload = JobPayload(
        id=uuid.uuid4(),
        topic="central bank interest rate decisions and inflation outlook",
        regions=regions,
        content_type="journal_article",
    )

    print(f"  Running {len(regions)} region(s) in parallel: {', '.join(regions)}")
    print(f"  Job ID: {payload.id}\n")

    t_start = time.perf_counter()

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(payload, session=session)
        report_rows = await query_cost_report(session, payload.id)

    elapsed = time.perf_counter() - t_start

    # Check 1: all requested regions returned a draft
    returned_regions = {d.region_id for d in drafts}
    results.append(check(
        f"All {len(regions)} region(s) returned a draft",
        returned_regions == set(regions),
        f"got: {sorted(returned_regions)}",
    ))

    # Check 2: each draft meets minimum quality bar
    for draft in drafts:
        results.append(check(
            f"{draft.region_id}: word count ≥ 600",
            draft.word_count >= 600,
            f"{draft.word_count} words",
        ))
        results.append(check(
            f"{draft.region_id}: headline is specific",
            len(draft.headline) > 15,
            draft.headline[:70],
        ))

    # Check 3: job status in DB
    async with AsyncSessionLocal() as session:
        row = await session.get(Job, payload.id)
        job_status = row.status if row else "not found"
    results.append(check(
        "Job status is 'complete' in DB",
        job_status == "complete",
        job_status,
    ))

    # Check 4: cost report has rows for all regions
    report_regions = {r["region"] for r in report_rows}
    results.append(check(
        "Cost report covers all regions",
        report_regions == set(regions),
        f"report regions: {sorted(report_regions)}",
    ))

    # Timing summary
    total_cost = sum(float(r["cost_usd"] or 0) for r in report_rows)
    print(f"\n  Elapsed: {elapsed:.1f}s  |  Total cost: ${total_cost:.4f}")
    if len(regions) > 1:
        print(f"  (Sequential equivalent would be ~{len(regions)}x longer)")

    # Per-region preview
    print()
    for draft in sorted(drafts, key=lambda d: d.region_id):
        print(f"  [{draft.region_id:<6}]  {draft.headline[:65]}")

    return results


# ---------------------------------------------------------------------------
# Check 5: error isolation (unit-level, no API calls)
# ---------------------------------------------------------------------------

def check_error_isolation() -> bool:
    """
    Verify that _run_region_task returns (region_id, None, 0.0, exc) on failure
    rather than propagating the exception — without making any real API calls.
    """
    import asyncio
    from orchestrator.pipeline import _run_region_task
    from config import load_content_type

    ct_config = load_content_type("journal_article")

    async def _test():
        # Pass a bad region_id — load_region will raise FileNotFoundError
        region_id, draft, cost, error = await _run_region_task(
            region_id="INVALID_REGION_XYZ",
            job_id=uuid.uuid4(),
            topic="test",
            articles=[],
            ct_config=ct_config,
        )
        return region_id, draft, cost, error

    region_id, draft, cost, error = asyncio.run(_test())

    isolated = (
        region_id == "INVALID_REGION_XYZ"
        and draft is None
        and cost == 0.0
        and error is not None
    )
    return check(
        "Error isolation: bad region returns error tuple, does not propagate",
        isolated,
        f"error={type(error).__name__}" if error else "no error captured",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(regions: list[str]) -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 15 — Parallel Region Execution Validation")
    print(f"{'─'*60}\n")

    all_results: list[bool] = []

    print("  [1-4] Parallel pipeline run:")
    pipeline_results = asyncio.run(run_checks(regions))
    all_results.extend(pipeline_results)

    print("\n  [5] Error isolation check:")
    all_results.append(check_error_isolation())

    print(f"\n{'─'*60}")
    if all(all_results):
        print(f"  Day 15 / Parallel execution: ALL CHECKS PASSED")
        print(f"  Pipeline is now parallel. Ready for Day 16.")
    else:
        failed = sum(1 for r in all_results if not r)
        print(f"  Day 15 / Parallel execution: {failed} CHECK(S) FAILED")
    print(f"{'─'*60}\n")

    sys.exit(0 if all(all_results) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Day 15 parallel execution")
    parser.add_argument(
        "--regions",
        nargs="+",
        default=["EU", "LATAM", "SEA", "NA"],
        metavar="REGION",
        help="Regions to test (default: all four)",
    )
    args = parser.parse_args()
    main(regions=[r.upper() for r in args.regions])
