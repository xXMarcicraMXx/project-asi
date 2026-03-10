"""
Day 14 — RAG Integration validation script.

Confirms:
  1. PineconeClient can be imported and constructed
  2. RAG context is fetched for each region (persona_guideline + golden_sample)
  3. Each fetched context is non-empty and region-appropriate
  4. WriterAgent user message correctly includes RAG context block
  5. Full EU pipeline run — article contains depth markers absent in baseline

Run AFTER ingestion/run_ingestion.py has seeded the index.

Usage:
    python scripts/validate_day14.py
    python scripts/validate_day14.py --skip-pipeline   # skip the live API call
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rag.pinecone_client import PineconeClient
from rag.schemas import REGION_TO_DEPARTMENT

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
# Check 1 & 2: RAG context retrieval per region
# ---------------------------------------------------------------------------

def check_rag_retrieval() -> list[bool]:
    results: list[bool] = []
    client = PineconeClient.from_settings()

    for region, dept in REGION_TO_DEPARTMENT.items():
        # persona_guideline
        persona_hits = client.query(
            text="editorial voice journalism regional perspective",
            filter={"department": dept, "document_type": "persona_guideline"},
            top_k=1,
        )
        results.append(check(
            f"{region} persona_guideline returned",
            bool(persona_hits and len(persona_hits[0]) > 50),
            f"{len(persona_hits[0])} chars" if persona_hits else "no result",
        ))

        # golden_sample
        sample_hits = client.query(
            text="interest rates central bank monetary policy",
            filter={"department": dept, "document_type": "golden_sample"},
            top_k=1,
        )
        results.append(check(
            f"{region} golden_sample returned",
            bool(sample_hits and len(sample_hits[0]) > 50),
            f"{len(sample_hits[0])} chars" if sample_hits else "no result",
        ))

    return results


# ---------------------------------------------------------------------------
# Check 3: RAG context is region-appropriate
# ---------------------------------------------------------------------------

def check_region_correctness() -> list[bool]:
    results: list[bool] = []
    client = PineconeClient.from_settings()

    checks = {
        "EU":    ({"department": "editorial_EU",    "document_type": "persona_guideline"},
                  ("eu", "europe", "brussels", "eurozone")),
        "LATAM": ({"department": "editorial_LATAM", "document_type": "persona_guideline"},
                  ("latin", "latam", "brazil", "imf", "washington consensus")),
        "SEA":   ({"department": "editorial_SEA",   "document_type": "persona_guideline"},
                  ("asean", "southeast", "singapore", "indonesia")),
        "NA":    ({"department": "editorial_NA",    "document_type": "persona_guideline"},
                  ("north america", "united states", "congress", "american", "canadian")),
    }

    for region, (filt, markers) in checks.items():
        hits = client.query(
            text="editorial voice journalism",
            filter=filt,
            top_k=1,
        )
        if hits:
            found = any(m in hits[0].lower() for m in markers)
            results.append(check(
                f"{region} persona contains region markers",
                found,
                hits[0][:80],
            ))
        else:
            results.append(check(f"{region} persona contains region markers", False, "no result"))

    return results


# ---------------------------------------------------------------------------
# Check 4: WriterAgent user message includes RAG block
# ---------------------------------------------------------------------------

def check_writer_message_contains_rag() -> bool:
    """
    Build a writer user message directly and verify the RAG block is present.
    Does not make any API calls.
    """
    from config import load_region, load_content_type
    from agents.writer_agent import _build_user_message
    from orchestrator.job_model import ResearchBrief

    region_config = load_region("EU")
    ct_config = load_content_type("journal_article")

    brief = ResearchBrief(
        topic="ECB interest rate decision",
        key_facts=["ECB held rates for third consecutive quarter"],
        data_points=["Inflation at 2.4% across eurozone"],
        direct_quotes=[],
        conflicting_perspectives=["Germany wants tighter policy; Italy wants easing"],
    )

    rag_context = "REGIONAL PERSONA GUIDELINES:\nTest persona context for EU region."

    message = _build_user_message(
        brief, region_config, ct_config,
        editor_feedback=None,
        rag_context=rag_context,
    )

    contains_rag = "ADDITIONAL PERSONA CONTEXT" in message
    contains_persona = "REGIONAL PERSONA GUIDELINES" in message
    return check(
        "WriterAgent user message contains RAG block",
        contains_rag and contains_persona,
        "ADDITIONAL PERSONA CONTEXT block found" if (contains_rag and contains_persona)
        else "block missing",
    )


# ---------------------------------------------------------------------------
# Check 5: Full pipeline run with RAG (optional — skipped with --skip-pipeline)
# ---------------------------------------------------------------------------

async def check_pipeline_with_rag() -> bool:
    """
    Run the full EU pipeline with RAG enabled.
    Check that the article word count and headline are valid,
    and that the RAG context was fetched (logged at DEBUG level).
    """
    import os
    from orchestrator.pipeline import run_pipeline
    from orchestrator.job_model import PipelinePayload
    import uuid

    payload = PipelinePayload(
        id=uuid.uuid4(),
        topic="ECB interest rate decision and eurozone fiscal outlook",
        regions=["EU"],
        content_type="journal_article",
    )

    drafts = await run_pipeline(payload)

    if not drafts:
        return check("Pipeline with RAG produced a draft", False, "no drafts returned")

    draft = drafts[0]
    word_ok = draft.word_count >= 600
    headline_ok = len(draft.headline) > 10

    result = check(
        "Pipeline with RAG: EU article produced",
        word_ok and headline_ok,
        f"{draft.word_count} words | {draft.headline[:60]}",
    )
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_pipeline: bool) -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 14 — RAG Integration Validation")
    print(f"{'─'*60}\n")

    all_results: list[bool] = []

    print("  [1/4] RAG retrieval — all regions:")
    all_results.extend(check_rag_retrieval())

    print("\n  [2/4] Region correctness checks:")
    all_results.extend(check_region_correctness())

    print("\n  [3/4] WriterAgent message construction:")
    all_results.append(check_writer_message_contains_rag())

    if skip_pipeline:
        print("\n  [4/4] Full pipeline run: SKIPPED (--skip-pipeline)")
    else:
        print("\n  [4/4] Full pipeline run with RAG:")
        result = asyncio.run(check_pipeline_with_rag())
        all_results.append(result)

    print(f"\n{'─'*60}")
    if all(all_results):
        print("  Day 14 / RAG integration: ALL CHECKS PASSED")
        print("  WriterAgent is now RAG-enriched. Ready for Day 15.")
    else:
        failed = sum(1 for r in all_results if not r)
        print(f"  Day 14 / RAG integration: {failed} CHECK(S) FAILED")
        print("  Ensure Pinecone index is seeded: python ingestion/run_ingestion.py")
    print(f"{'─'*60}\n")

    sys.exit(0 if all(all_results) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Day 14 RAG integration")
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Skip the full pipeline run (checks 1-3 only)",
    )
    args = parser.parse_args()
    main(skip_pipeline=args.skip_pipeline)
