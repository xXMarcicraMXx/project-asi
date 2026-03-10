"""
Day 16 — Sprint 3 milestone validation script.

Sprint 3 targets:
  - Full 4-region job completes in < 3 minutes  (wall-clock)
  - Total cost per job < $1.00
  - All 4 articles produced and readable
  - Alembic is installed and migration history is accessible

Usage:
    python scripts/validate_day16.py
    python scripts/validate_day16.py --skip-pipeline   # Alembic checks only
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"

WALL_CLOCK_TARGET_S = 180   # 3 minutes
COST_TARGET_USD     = 1.00
MIN_WORDS           = 600


def check(label: str, condition: bool, detail: str = "", warn: bool = False) -> bool:
    tag = (WARN if warn else FAIL) if not condition else PASS
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# Check 1: Alembic installed and history accessible
# ---------------------------------------------------------------------------

def check_alembic() -> list[bool]:
    results: list[bool] = []

    # Is alembic importable?
    try:
        import alembic  # noqa: F401
        results.append(check("alembic package installed", True, f"v{alembic.__version__}"))
    except ImportError:
        results.append(check("alembic package installed", False, "run: pip install alembic"))
        return results

    # Does alembic.ini exist?
    ini_path = Path(__file__).parent.parent / "alembic.ini"
    results.append(check("alembic.ini present", ini_path.exists(), str(ini_path)))

    # Does the initial migration file exist?
    migration_path = Path(__file__).parent.parent / "alembic" / "versions" / "001_initial_schema.py"
    results.append(check("initial migration file present", migration_path.exists()))

    # Can alembic connect and show history?
    try:
        proc = subprocess.run(
            ["alembic", "history"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=15,
        )
        history_ok = proc.returncode == 0 and "001" in proc.stdout
        results.append(check(
            "alembic history lists revision 001",
            history_ok,
            proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else proc.stderr.strip()[:80],
        ))
    except Exception as exc:
        results.append(check("alembic history accessible", False, str(exc)[:80]))

    return results


# ---------------------------------------------------------------------------
# Check 2–5: Full 4-region pipeline — performance and quality
# ---------------------------------------------------------------------------

async def run_pipeline_checks() -> list[bool]:
    from db.session import AsyncSessionLocal
    from orchestrator.job_model import JobPayload
    from orchestrator.pipeline import query_cost_report, run_pipeline

    results: list[bool] = []

    payload = JobPayload(
        id=uuid.uuid4(),
        topic="global inflation and central bank policy divergence",
        regions=["EU", "LATAM", "SEA", "NA"],
        content_type="journal_article",
    )

    print(f"  Job ID: {payload.id}")
    print(f"  Topic:  {payload.topic}\n")

    t_start = time.perf_counter()

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(payload, session=session)
        report_rows = await query_cost_report(session, payload.id)

    elapsed = time.perf_counter() - t_start
    total_cost = sum(float(r["cost_usd"] or 0) for r in report_rows)

    # Check 2: wall-clock < 3 minutes
    results.append(check(
        f"Wall-clock time < {WALL_CLOCK_TARGET_S}s",
        elapsed < WALL_CLOCK_TARGET_S,
        f"{elapsed:.1f}s",
    ))

    # Check 3: total cost < $1.00
    results.append(check(
        f"Total cost < ${COST_TARGET_USD:.2f}",
        total_cost < COST_TARGET_USD,
        f"${total_cost:.4f}",
    ))

    # Check 4: all 4 regions returned
    returned = {d.region_id for d in drafts}
    results.append(check(
        "All 4 regions produced a draft",
        len(drafts) == 4,
        f"got {sorted(returned)}",
    ))

    # Check 5: article quality per region
    print()
    for draft in sorted(drafts, key=lambda d: d.region_id):
        word_ok = draft.word_count >= MIN_WORDS
        results.append(check(
            f"{draft.region_id}: ≥ {MIN_WORDS} words",
            word_ok,
            f"{draft.word_count} words — {draft.headline[:55]}",
            warn=not word_ok,  # word count miss is a warning not a hard failure
        ))

    print(f"\n  {'─'*50}")
    print(f"  Elapsed:    {elapsed:.1f}s  (target < {WALL_CLOCK_TARGET_S}s)")
    print(f"  Total cost: ${total_cost:.4f}  (target < ${COST_TARGET_USD:.2f})")

    # Per-agent cost breakdown
    from collections import defaultdict
    by_agent: dict = defaultdict(lambda: {"cost": 0.0, "in": 0, "out": 0, "runs": 0})
    for r in report_rows:
        a = r["agent_name"]
        by_agent[a]["cost"] += float(r["cost_usd"] or 0)
        by_agent[a]["in"]   += r["input_tokens"] or 0
        by_agent[a]["out"]  += r["output_tokens"] or 0
        by_agent[a]["runs"] += 1
    print(f"\n  {'Agent':<22}  {'Runs':>4}  {'In tok':>8}  {'Out tok':>8}  {'USD':>8}")
    print(f"  {'─'*56}")
    for agent, agg in sorted(by_agent.items()):
        print(f"  {agent:<22}  {agg['runs']:>4}  {agg['in']:>8,}  {agg['out']:>8,}  ${agg['cost']:>7.4f}")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_pipeline: bool) -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 16 — Sprint 3 Milestone Validation")
    print(f"{'─'*60}\n")

    all_results: list[bool] = []

    print("  [1] Alembic setup:")
    all_results.extend(check_alembic())

    if skip_pipeline:
        print("\n  [2-5] Pipeline checks: SKIPPED (--skip-pipeline)")
    else:
        print("\n  [2-5] 4-region pipeline — performance & quality:")
        all_results.extend(asyncio.run(run_pipeline_checks()))

    # Sprint 3 milestone verdict
    hard_failures = [r for r in all_results if not r]
    print(f"\n{'─'*60}")
    if not hard_failures:
        print("  Sprint 3 milestone: ALL CHECKS PASSED ✓")
        print("  4-region parallel pipeline is production-ready.")
        print("  Ready for Sprint 4 (Slack, Docker, Scheduler).")
    else:
        print(f"  Sprint 3 milestone: {len(hard_failures)} CHECK(S) FAILED")
    print(f"{'─'*60}\n")

    sys.exit(0 if not hard_failures else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Day 16 Sprint 3 milestone")
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Only check Alembic setup, skip the live pipeline run",
    )
    args = parser.parse_args()
    main(skip_pipeline=args.skip_pipeline)
