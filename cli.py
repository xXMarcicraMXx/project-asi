"""
ASI CLI — entry point for triggering pipeline runs.

Usage:
    python cli.py run --topic "EU elections" --region EU
    python cli.py run --topic "AI regulation" --region EU --region NA
    python cli.py run --topic "custom topic" --region EU --source-text "paste article text here"

Options:
    --topic         Topic to research and write about (required)
    --region        Region ID to produce an article for; repeat for multiple regions
    --content-type  Content type config to use (default: journal_article)
    --source-text   Skip RSS fetch and use this raw text as the source
    --output-dir    Directory to write article markdown files (optional)
    --log-plain     Use plain text logging instead of JSON (useful for local dev)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from config import load_settings
from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import query_cost_report, run_pipeline
from utils.log import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="ASI — autonomous multi-regional article engine",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run the article pipeline")
    run_cmd.add_argument("--topic", required=True, help="Topic to write about")
    run_cmd.add_argument(
        "--region",
        dest="regions",
        action="append",
        required=True,
        metavar="REGION",
        help="Region ID (EU | LATAM | SEA | NA). Repeat for multiple regions.",
    )
    run_cmd.add_argument(
        "--content-type",
        default="journal_article",
        help="Content type config name (default: journal_article)",
    )
    run_cmd.add_argument(
        "--source-text",
        default=None,
        help="Raw source text to use instead of RSS fetch",
    )
    run_cmd.add_argument(
        "--output-dir",
        default=os.environ.get("ASI_OUTPUT_DIR"),
        help="Directory to write output markdown files",
    )
    run_cmd.add_argument(
        "--log-plain",
        action="store_true",
        help="Human-readable log format (default: JSON)",
    )
    return parser


async def cmd_run(args: argparse.Namespace) -> None:
    payload = JobPayload(
        topic=args.topic,
        content_type=args.content_type,
        regions=[r.upper() for r in args.regions],
    )

    logger.info(
        "cli_run",
        extra={
            "job_id": str(payload.id),
            "topic": payload.topic,
            "regions": payload.regions,
        },
    )

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(
            payload,
            session=session,
            source_text=args.source_text,
        )

        # Cost report — query while session is still open
        report_rows = await query_cost_report(session, payload.id)

    # ── Print articles ──────────────────────────────────────────────────────
    for draft in drafts:
        print("\n" + "=" * 72)
        print(f"REGION: {draft.region_id}  |  {draft.word_count} words")
        print("=" * 72)
        print(draft.body)

        if args.output_dir:
            out_dir = Path(args.output_dir) / str(payload.id)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{draft.region_id.lower()}.md"
            out_file.write_text(draft.body, encoding="utf-8")
            print(f"\n  Saved to: {out_file}")

    # ── Cost report ─────────────────────────────────────────────────────────
    _print_cost_report(report_rows)

    print(f"\nJob complete. {len(drafts)} article(s) produced.")


def _print_cost_report(rows: list[dict]) -> None:
    """Print a formatted token-usage and cost summary table."""
    if not rows:
        return

    # Aggregate by (region, agent_name)
    totals: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["region"], row["agent_name"])
        if key not in totals:
            totals[key] = {"in": 0, "out": 0, "cost": 0.0, "runs": 0, "ms": 0}
        totals[key]["in"]   += row["input_tokens"] or 0
        totals[key]["out"]  += row["output_tokens"] or 0
        totals[key]["cost"] += float(row["cost_usd"] or 0)
        totals[key]["runs"] += 1
        totals[key]["ms"]   += row["duration_ms"] or 0

    print("\n" + "─" * 72)
    print(f"{'COST REPORT':^72}")
    print("─" * 72)
    print(f"  {'Region':<8}  {'Agent':<18}  {'Runs':>4}  {'In Tok':>8}  {'Out Tok':>8}  {'USD':>8}")
    print("  " + "─" * 66)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    for (region, agent), agg in sorted(totals.items()):
        print(
            f"  {region:<8}  {agent:<18}  {agg['runs']:>4}  "
            f"{agg['in']:>8,}  {agg['out']:>8,}  ${agg['cost']:>7.4f}"
        )
        total_cost += agg["cost"]
        total_in += agg["in"]
        total_out += agg["out"]

    print("  " + "─" * 66)
    print(
        f"  {'TOTAL':<8}  {'':18}  {'':4}  "
        f"{total_in:>8,}  {total_out:>8,}  ${total_cost:>7.4f}"
    )
    print("─" * 72)


def main() -> None:
    # Parse args first so --log-plain is available before setup
    parser = build_parser()
    args = parser.parse_args()

    # Initialise logging from settings
    settings = load_settings()
    setup_logging(
        level=settings.logging.level,
        json_format=not getattr(args, "log_plain", False),
    )

    if args.command == "run":
        asyncio.run(cmd_run(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
