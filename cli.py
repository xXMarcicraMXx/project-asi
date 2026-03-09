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
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import run_pipeline


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
    return parser


async def cmd_run(args: argparse.Namespace) -> None:
    payload = JobPayload(
        topic=args.topic,
        content_type=args.content_type,
        regions=[r.upper() for r in args.regions],
    )

    print(f"\nJob {payload.id}")
    print(f"  topic        : {payload.topic}")
    print(f"  regions      : {', '.join(payload.regions)}")
    print(f"  content_type : {payload.content_type}")
    print()

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(
            payload,
            session=session,
            source_text=args.source_text,
        )

    # Print and optionally save each draft
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

    print(f"\nJob complete. {len(drafts)} article(s) produced.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(cmd_run(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
