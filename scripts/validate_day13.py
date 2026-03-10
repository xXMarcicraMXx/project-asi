"""
Day 13 — Pinecone Ingestion validation script.

Confirms:
  1. Index exists and is reachable
  2. Each region returns a result for document_type=persona_guideline
  3. Each region returns a result for document_type=golden_sample
  4. Results are region-correct (EU query returns EU content)
  5. Cross-region contamination check: EU filter never returns LATAM content

Run AFTER ingestion/run_ingestion.py.

Usage:
    python scripts/validate_day13.py
"""

from __future__ import annotations

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


def main() -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 13 — Pinecone Ingestion Validation")
    print(f"{'─'*60}\n")

    client = PineconeClient.from_settings()
    results = []

    # ── Check 1: index is reachable ──────────────────────────────────────────
    try:
        from pinecone import Pinecone
        import os
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        existing = {i.name for i in pc.list_indexes()}
        index_name = client._index_name
        results.append(
            check("index exists", index_name in existing, index_name)
        )
    except Exception as exc:
        results.append(check("index reachable", False, str(exc)))
        _finish(results)
        return

    # ── Check 2 & 3: each region returns results for both doc types ──────────
    for region, dept in REGION_TO_DEPARTMENT.items():
        for doc_type in ("persona_guideline", "golden_sample"):
            hits = client.query(
                text="editorial voice journalism regional perspective",
                filter={"department": dept, "document_type": doc_type},
                top_k=1,
            )
            results.append(
                check(
                    f"{region} {doc_type} returns a result",
                    len(hits) > 0 and len(hits[0]) > 50,
                    f"{len(hits[0])} chars" if hits else "no results",
                )
            )

    # ── Check 4: result is region-correct ────────────────────────────────────
    # EU persona should mention EU/Europe/Brussels — not São Paulo or Singapore
    eu_hits = client.query(
        text="journalism editorial voice",
        filter={"department": "editorial_EU", "document_type": "persona_guideline"},
        top_k=1,
    )
    if eu_hits:
        text_lower = eu_hits[0].lower()
        eu_markers = any(kw in text_lower for kw in ("eu", "europe", "brussels", "eurozone", "treaty"))
        results.append(check("EU persona contains EU markers", eu_markers, eu_hits[0][:80]))
    else:
        results.append(check("EU persona contains EU markers", False, "no result"))

    latam_hits = client.query(
        text="journalism editorial voice",
        filter={"department": "editorial_LATAM", "document_type": "persona_guideline"},
        top_k=1,
    )
    if latam_hits:
        text_lower = latam_hits[0].lower()
        latam_markers = any(kw in text_lower for kw in ("latin", "latam", "brazil", "imf", "austerity", "washington consensus"))
        results.append(check("LATAM persona contains LATAM markers", latam_markers, latam_hits[0][:80]))
    else:
        results.append(check("LATAM persona contains LATAM markers", False, "no result"))

    # ── Check 5: cross-region contamination ──────────────────────────────────
    # EU filter must not return LATAM or SEA content
    eu_golden = client.query(
        text="interest rates central bank",
        filter={"department": "editorial_EU", "document_type": "golden_sample"},
        top_k=1,
    )
    if eu_golden:
        text_lower = eu_golden[0].lower()
        no_contamination = not any(
            kw in text_lower for kw in ("são paulo", "asean", "banxico", "rupiah")
        )
        results.append(
            check("EU golden sample has no LATAM/SEA contamination", no_contamination)
        )
    else:
        results.append(check("EU golden sample contamination check", False, "no result"))

    # ── Result ───────────────────────────────────────────────────────────────
    _finish(results)


def _finish(results: list[bool]) -> None:
    all_passed = all(results)
    print(f"\n{'─'*60}")
    if all_passed:
        print(f"  Day 13 / Pinecone ingestion: ALL CHECKS PASSED")
        print(f"  Index is seeded and ready for Day 14 RAG integration.")
    else:
        print(f"  Day 13 / Pinecone ingestion: CHECKS FAILED")
        print(f"  Run: python ingestion/run_ingestion.py")
    print(f"{'─'*60}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
