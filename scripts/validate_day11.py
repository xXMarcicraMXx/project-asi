"""
Day 11 — Sprint 2 Milestone validation script.

Runs the full pipeline for a single EU region article and verifies:

  1. Pipeline completes without error
  2. Job row written with status='complete'
  3. content_piece status is 'approved' or 'human_review'
  4. agent_runs rows exist for every agent call (≥3)
  5. feedback_loops rows exist (≥1 editor verdict)
  6. Article is ≥600 words
  7. Article was saved to --output-dir (if provided)
  8. Cost report printed

This script is the automated gate for the Sprint 2 milestone.
Human eye-check: read the printed EU article and confirm the voice is
distinctly European in framing, vocabulary, and editorial posture.

Usage:
    python scripts/validate_day11.py
    python scripts/validate_day11.py --topic "AI regulation" --output-dir ./output
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import func, select

from config import load_settings
from db.models import AgentRun, Brief, ContentPiece, FeedbackLoop, Job
from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import query_cost_report, run_pipeline
from utils.log import setup_logging


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return condition


async def main(topic: str, output_dir: str | None) -> None:
    settings = load_settings()
    setup_logging(level=settings.logging.level, json_format=False)

    payload = JobPayload(
        topic=topic,
        content_type="journal_article",
        regions=["EU"],
    )

    print(f"\n{'─'*60}")
    print(f"  Day 11 — Sprint 2 Milestone Validation")
    print(f"{'─'*60}")
    print(f"  job_id  : {payload.id}")
    print(f"  topic   : {topic}")
    print(f"  region  : EU\n")

    # ── Run pipeline ────────────────────────────────────────────────────────
    out_dir = Path(output_dir) / str(payload.id) if output_dir else None
    if output_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(
            payload,
            session=session,
            source_text=None,
        )

        # ── Query DB audit trail ─────────────────────────────────────────────
        job_row_result = await session.execute(
            select(Job).where(Job.id == payload.id, Job.project == "asi")
        )
        job_row = job_row_result.scalar_one_or_none()

        pieces_result = await session.execute(
            select(ContentPiece)
            .join(Brief, ContentPiece.brief_id == Brief.id)
            .join(Job, Brief.job_id == Job.id)
            .where(Brief.job_id == payload.id, Job.project == "asi")
        )
        pieces = pieces_result.scalars().all()
        piece = pieces[0] if pieces else None

        runs_result = await session.execute(
            select(AgentRun)
            .join(ContentPiece, AgentRun.content_piece_id == ContentPiece.id)
            .join(Brief, ContentPiece.brief_id == Brief.id)
            .join(Job, Brief.job_id == Job.id)
            .where(Brief.job_id == payload.id, Job.project == "asi")
        )
        runs = runs_result.scalars().all()

        loops_result = await session.execute(
            select(FeedbackLoop)
            .join(ContentPiece, FeedbackLoop.content_piece_id == ContentPiece.id)
            .join(Brief, ContentPiece.brief_id == Brief.id)
            .join(Job, Brief.job_id == Job.id)
            .where(Brief.job_id == payload.id, Job.project == "asi")
        )
        loops = loops_result.scalars().all()

        cost_rows = await query_cost_report(session, payload.id)

    draft = drafts[0]

    # ── Assertions ───────────────────────────────────────────────────────────
    print(f"\n  Checks")
    print(f"  {'─'*54}")
    results = [
        check("pipeline returned 1 draft", len(drafts) == 1),
        check("job.status = complete", job_row and job_row.status == "complete"),
        check(
            "content_piece status is approved or human_review",
            piece is not None and piece.status in ("approved", "human_review"),
            f"status={piece.status if piece else 'MISSING'}",
        ),
        check(
            "agent_runs ≥ 3 rows (research + write + edit)",
            len(runs) >= 3,
            f"found {len(runs)}",
        ),
        check(
            "feedback_loops ≥ 1 editor verdict",
            len(loops) >= 1,
            f"found {len(loops)}",
        ),
        check(
            "article ≥ 600 words",
            draft.word_count >= 600,
            f"{draft.word_count} words",
        ),
    ]

    if out_dir:
        md_file = out_dir / "eu.md"
        if output_dir:
            draft.body  # ensure body is populated
            md_file.write_text(draft.body, encoding="utf-8")
        results.append(
            check("markdown file written to output-dir", md_file.exists(), str(md_file))
        )

    # ── Cost report ──────────────────────────────────────────────────────────
    print()
    _print_cost_table(cost_rows)

    # ── Article preview ──────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  ARTICLE — {piece.status.upper() if piece else '?'} after {piece.iteration_count if piece else '?'} iteration(s)")
    print(f"{'─'*60}")
    print(f"\n  Headline: {draft.headline}\n")
    preview_lines = draft.body.splitlines()[:30]
    print("\n".join(f"  {l}" for l in preview_lines))
    if len(draft.body.splitlines()) > 30:
        print(f"\n  ... ({draft.word_count} words total — full text in output file)")

    # ── Result ───────────────────────────────────────────────────────────────
    all_passed = all(results)
    print(f"\n{'─'*60}")
    if all_passed:
        print(f"  Day 11 / Sprint 2 milestone: ALL CHECKS PASSED")
        print(f"\n  HUMAN CHECK REQUIRED:")
        print(f"  Read the article above. Confirm:")
        print(f"  - Framing leads with regulatory/institutional dimension")
        print(f"  - Voice is measured and formally structured")
        print(f"  - References EU institutions, member-state dynamics, or treaty context")
        print(f"  - Avoids American idioms and sensationalist language")
    else:
        print(f"  Day 11 / Sprint 2 milestone: CHECKS FAILED — review output above")
    print(f"{'─'*60}\n")

    sys.exit(0 if all_passed else 1)


def _print_cost_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no cost data)")
        return
    totals: dict[str, dict] = {}
    for row in rows:
        key = row["agent_name"]
        if key not in totals:
            totals[key] = {"in": 0, "out": 0, "cost": 0.0, "runs": 0}
        totals[key]["in"]   += row["input_tokens"] or 0
        totals[key]["out"]  += row["output_tokens"] or 0
        totals[key]["cost"] += float(row["cost_usd"] or 0)
        totals[key]["runs"] += 1

    print(f"  Cost breakdown")
    print(f"  {'─'*54}")
    print(f"  {'Agent':<20}  {'Runs':>4}  {'In Tok':>8}  {'Out Tok':>8}  {'USD':>8}")
    total_cost = 0.0
    for agent, agg in sorted(totals.items()):
        print(f"  {agent:<20}  {agg['runs']:>4}  {agg['in']:>8,}  {agg['out']:>8,}  ${agg['cost']:>7.4f}")
        total_cost += agg["cost"]
    print(f"  {'─'*54}")
    print(f"  {'TOTAL':<20}  {'':4}  {'':8}  {'':8}  ${total_cost:>7.4f}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Day 11 milestone validation")
    parser.add_argument(
        "--topic",
        default="AI regulation",
        help="Topic to run the pipeline on (default: 'AI regulation')",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Save the article markdown to this directory",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(args.topic, args.output_dir))
