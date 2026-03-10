"""
Document preparation helpers for Pinecone ingestion.

Responsibilities:
- Build well-formed document dicts (id, text, metadata) ready for PineconeClient.upsert()
- Validate metadata completeness before any network call
- Chunk long texts so no single vector exceeds the embedding model's token limit

Used by ingestion/run_ingestion.py. Not used at query time.
"""

from __future__ import annotations

from rag.schemas import (
    ACCESS_LEVELS,
    CONTENT_TYPES,
    DEPARTMENTS,
    DOCUMENT_TYPES,
    FIELD_ACCESS_LEVEL,
    FIELD_CONTENT_TYPE,
    FIELD_DEPARTMENT,
    FIELD_DOCUMENT_TYPE,
)

# Pinecone's llama-text-embed-v2 accepts up to ~512 tokens.
# ~1800 characters is a safe conservative limit for most English prose.
_MAX_CHUNK_CHARS = 1800


def build_document(
    *,
    doc_id: str,
    text: str,
    department: str,
    document_type: str,
    content_type: str,
    access_level: str = "internal_only",
) -> dict:
    """
    Build a single document dict for PineconeClient.upsert().
    Raises ValueError on invalid metadata values.
    """
    _validate_metadata(department, document_type, content_type, access_level)
    return {
        "id": doc_id,
        "text": text.strip(),
        "metadata": {
            FIELD_DEPARTMENT:    department,
            FIELD_DOCUMENT_TYPE: document_type,
            FIELD_CONTENT_TYPE:  content_type,
            FIELD_ACCESS_LEVEL:  access_level,
        },
    }


def chunk_document(doc: dict, max_chars: int = _MAX_CHUNK_CHARS) -> list[dict]:
    """
    Split a document whose text exceeds max_chars into overlapping chunks.
    Each chunk inherits the parent's metadata and gets a suffixed ID.

    If the text fits within max_chars, returns the original doc unchanged
    (as a single-element list).
    """
    text = doc["text"]
    if len(text) <= max_chars:
        return [doc]

    chunks: list[dict] = []
    overlap = max_chars // 5   # 20% overlap between chunks
    start = 0
    chunk_idx = 0

    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({
                "id": f"{doc['id']}-c{chunk_idx}",
                "text": chunk_text,
                "metadata": doc["metadata"].copy(),
            })
        start += max_chars - overlap
        chunk_idx += 1

    return chunks


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_metadata(
    department: str,
    document_type: str,
    content_type: str,
    access_level: str,
) -> None:
    errors = []
    if department not in DEPARTMENTS:
        errors.append(f"department '{department}' not in {sorted(DEPARTMENTS)}")
    if document_type not in DOCUMENT_TYPES:
        errors.append(f"document_type '{document_type}' not in {sorted(DOCUMENT_TYPES)}")
    if content_type not in CONTENT_TYPES:
        errors.append(f"content_type '{content_type}' not in {sorted(CONTENT_TYPES)}")
    if access_level not in ACCESS_LEVELS:
        errors.append(f"access_level '{access_level}' not in {sorted(ACCESS_LEVELS)}")
    if errors:
        raise ValueError("Invalid Pinecone metadata:\n" + "\n".join(f"  - {e}" for e in errors))
