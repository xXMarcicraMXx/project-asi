"""
Pinecone metadata schema constants for Project ASI.

Every document upserted into asi-personas must carry all four keys.
Retrieval always filters on department + document_type + content_type
to prevent cross-region and cross-project contamination.

Schema (per spec Section 10):
    department    — which regional editorial desk owns this document
    document_type — what kind of document this is
    content_type  — which output format it applies to
    access_level  — data governance tier
"""

# ── Metadata field names ──────────────────────────────────────────────────────

FIELD_DEPARTMENT    = "department"
FIELD_DOCUMENT_TYPE = "document_type"
FIELD_CONTENT_TYPE  = "content_type"
FIELD_ACCESS_LEVEL  = "access_level"

# ── Allowed values ────────────────────────────────────────────────────────────

DEPARTMENTS = frozenset({
    "editorial_EU",
    "editorial_LATAM",
    "editorial_SEA",
    "editorial_NA",
})

DOCUMENT_TYPES = frozenset({
    "persona_guideline",    # defines regional editorial voice in depth
    "golden_sample",        # hand-written exemplar article in that voice
    "formatting_template",  # structural/formatting guidance
})

CONTENT_TYPES = frozenset({
    "journal_article",
    "daily_brief",          # Evolution 1
})

ACCESS_LEVELS = frozenset({
    "public",
    "internal_only",
})

# ── Region → department mapping ───────────────────────────────────────────────

REGION_TO_DEPARTMENT: dict[str, str] = {
    "EU":   "editorial_EU",
    "LATAM": "editorial_LATAM",
    "SEA":  "editorial_SEA",
    "NA":   "editorial_NA",
}
