"""
Day 12 — Multi-Region Sequential validation script.

Runs the full pipeline for all 4 regions on the same topic and confirms:

  1. Pipeline returns 4 drafts
  2. 4 content_piece rows in DB, all linked to the same job
  3. All 4 content_pieces are approved or human_review
  4. All 4 headlines are distinct (basic differentiation check)
  5. All 4 articles are >= 600 words
  6. Cost report printed per region

This is the Sprint 3 Day 12 gate.

Usage:
    python scripts/validate_day12.py
    python scripts/validate_day12.py --topic "global trade" --output-dir ./output
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from config import load_settings
from db.models import Brief, ContentPiece, Job
from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import query_cost_report, run_pipeline
from utils.log import setup_logging


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

REGIONS = ["EU", "LATAM", "SEA", "NA"]


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
        regions=REGIONS,
    )

    print(f"\n{'─'*60}")
    print(f"  Day 12 — Multi-Region Sequential Validation")
    print(f"{'─'*60}")
    print(f"  job_id  : {payload.id}")
    print(f"  topic   : {topic}")
    print(f"  regions : {', '.join(REGIONS)}\n")

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(
            payload,
            session=session,
            source_text=None,
        )

        # Query all content_pieces for this job
        pieces_result = await session.execute(
            select(ContentPiece)
            .join(Brief, ContentPiece.brief_id == Brief.id)
            .join(Job, Brief.job_id == Job.id)
            .where(Brief.job_id == payload.id, Job.project == "asi")
            .order_by(ContentPiece.region)
        )
        pieces = pieces_result.scalars().all()

        cost_rows = await query_cost_report(session, payload.id)

    # ── Checks ───────────────────────────────────────────────────────────────
    print(f"  Checks")
    print(f"  {'─'*54}")

    headlines = [d.headline for d in drafts]
    results = [
        check("pipeline returned 4 drafts", len(drafts) == 4, f"got {len(drafts)}"),
        check(
            "4 content_pieces in DB",
            len(pieces) == 4,
            f"found {len(pieces)}",
        ),
        check(
            "all pieces approved or human_review",
            all(p.status in ("approved", "human_review") for p in pieces),
            ", ".join(f"{p.region}={p.status}" for p in pieces),
        ),
        check(
            "all 4 headlines are distinct",
            len(set(headlines)) == 4,
            f"{len(set(headlines))} unique",
        ),
        check(
            "all articles >= 600 words",
            all(d.word_count >= 600 for d in drafts),
            ", ".join(f"{d.region_id}={d.word_count}w" for d in drafts),
        ),
    ]

    # ── Cost report ──────────────────────────────────────────────────────────
    print()
    _print_cost_table(cost_rows)

    # ── Article headlines + opening line per region ──────────────────────────
    print(f"\n{'─'*60}")
    print(f"  REGIONAL DIFFERENTIATION PREVIEW")
    print(f"{'─'*60}")
    for draft in drafts:
        piece = next((p for p in pieces if p.region == draft.region_id), None)
        status = piece.status.upper() if piece else "?"
        first_para = next(
            (l.strip() for l in draft.body.splitlines() if l.strip() and not l.startswith("#")),
            ""
        )
        print(f"\n  [{draft.region_id}]  {status}  {draft.word_count} words")
        print(f"  Headline : {draft.headline}")
        print(f"  Opens    : {first_para[:160]}{'...' if len(first_para) > 160 else ''}")

    # ── Save output files if requested ──────────────────────────────────────
    if output_dir:
        out_dir = Path(output_dir) / str(payload.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        for draft in drafts:
            (out_dir / f"{draft.region_id.lower()}.md").write_text(draft.body, encoding="utf-8")
        print(f"\n  Articles saved to: {out_dir}")

    # ── Result ───────────────────────────────────────────────────────────────
    all_passed = all(results)
    print(f"\n{'─'*60}")
    if all_passed:
        print(f"  Day 12 / Multi-Region: ALL CHECKS PASSED")
        print(f"\n  HUMAN CHECK: read the 4 opening lines above.")
        print(f"  EU should lead with institutional/regulatory framing.")
        print(f"  LATAM should foreground political economy and sovereignty.")
        print(f"  SEA should open with strategic or commercial implications.")
        print(f"  NA should lead with direct domestic impact.")
    else:
        print(f"  Day 12 / Multi-Region: CHECKS FAILED — review output above")
    print(f"{'─'*60}\n")

    sys.exit(0 if all_passed else 1)


def _print_cost_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no cost data)")
        return

    totals: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["region"], row["agent_name"])
        if key not in totals:
            totals[key] = {"in": 0, "out": 0, "cost": 0.0, "runs": 0}
        totals[key]["in"]   += row["input_tokens"] or 0
        totals[key]["out"]  += row["output_tokens"] or 0
        totals[key]["cost"] += float(row["cost_usd"] or 0)
        totals[key]["runs"] += 1

    print(f"  Cost breakdown")
    print(f"  {'─'*62}")
    print(f"  {'Region':<8}  {'Agent':<20}  {'Runs':>4}  {'In Tok':>8}  {'Out Tok':>7}  {'USD':>8}")
    total_cost = 0.0
    current_region = None
    for (region, agent), agg in sorted(totals.items()):
        if region != current_region:
            if current_region is not None:
                print()
            current_region = region
        print(
            f"  {region:<8}  {agent:<20}  {agg['runs']:>4}  "
            f"{agg['in']:>8,}  {agg['out']:>7,}  ${agg['cost']:>7.4f}"
        )
        total_cost += agg["cost"]
    print(f"  {'─'*62}")
    print(f"  {'TOTAL':<30}  {'':4}  {'':8}  {'':7}  ${total_cost:>7.4f}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Day 12 multi-region validation")
    parser.add_argument(
        "--topic",
        default="interest rates",
        help="Topic to run the pipeline on (default: 'interest rates')",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Save article markdown files to this directory",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(args.topic, args.output_dir))
