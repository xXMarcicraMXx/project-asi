"""
PineconeClient — upsert and metadata-filtered retrieval for ASI.

Embeddings are generated via Pinecone's own inference API
(no external embedding provider required).

Usage:
    client = PineconeClient.from_settings()

    # Seed
    client.ensure_index()
    client.upsert([{"id": "eu-persona-1", "text": "...", "metadata": {...}}])

    # Retrieve at write time
    contexts = client.query(
        text="AI regulation in Europe",
        filter={"department": "editorial_EU", "document_type": "persona_guideline"},
    )
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from config import load_settings
from rag.schemas import FIELD_DEPARTMENT, FIELD_DOCUMENT_TYPE

logger = logging.getLogger(__name__)


class PineconeClient:
    """
    Thin wrapper around the Pinecone v4 SDK.

    All retrieval calls include the full metadata filter so that ASI
    documents never bleed into ORACLE's query results (separate index,
    but belt-and-suspenders).
    """

    def __init__(
        self,
        *,
        index_name: str,
        embedding_model: str,
        embedding_dimension: int,
        top_k: int,
    ) -> None:
        self._pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        self._index_name = index_name
        self._embedding_model = embedding_model
        self._embedding_dimension = embedding_dimension
        self._top_k = top_k
        self._index: Any = None

    @classmethod
    def from_settings(cls) -> "PineconeClient":
        """Construct from config/settings.yaml."""
        s = load_settings()
        return cls(
            index_name=os.environ.get("PINECONE_INDEX_NAME", s.pinecone.index_name),
            embedding_model=s.pinecone.embedding_model,
            embedding_dimension=s.pinecone.embedding_dimension,
            top_k=s.pinecone.top_k,
        )

    # ── Index management ──────────────────────────────────────────────────────

    def ensure_index(
        self,
        cloud: str = "aws",
        region: str = "us-east-1",
    ) -> None:
        """
        Create the Pinecone index if it does not already exist.
        Blocks until the index is ready.  Safe to call repeatedly.
        """
        existing = {i.name for i in self._pc.list_indexes()}
        if self._index_name in existing:
            logger.info(
                "pinecone_index_exists",
                extra={"index": self._index_name},
            )
            return

        logger.info(
            "pinecone_index_creating",
            extra={
                "index": self._index_name,
                "dimension": self._embedding_dimension,
                "cloud": cloud,
                "region": region,
            },
        )
        self._pc.create_index(
            name=self._index_name,
            dimension=self._embedding_dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud=cloud, region=region),
        )

        # Wait until ready
        for _ in range(60):
            status = self._pc.describe_index(self._index_name).status
            if status.get("ready"):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Pinecone index '{self._index_name}' did not become ready in 120s")

        logger.info("pinecone_index_ready", extra={"index": self._index_name})

    # ── Embedding ─────────────────────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Pinecone inference API."""
        result = self._pc.inference.embed(
            model=self._embedding_model,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"},
        )
        return [list(item.values) for item in result]

    # ── Upsert ────────────────────────────────────────────────────────────────

    def upsert(self, documents: list[dict]) -> int:
        """
        Embed and upsert a batch of documents.

        Each document must have:
            id       : str   — unique vector ID
            text     : str   — the text to embed and store
            metadata : dict  — must include all 4 schema fields

        Returns the number of vectors upserted.
        """
        if not documents:
            return 0

        texts = [d["text"] for d in documents]
        embeddings = self._embed(texts)

        vectors = [
            {
                "id": doc["id"],
                "values": emb,
                "metadata": {**doc["metadata"], "text": doc["text"]},
            }
            for doc, emb in zip(documents, embeddings)
        ]

        self._get_index().upsert(vectors=vectors)
        logger.info(
            "pinecone_upsert",
            extra={"count": len(vectors), "index": self._index_name},
        )
        return len(vectors)

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        filter: dict,
        top_k: int | None = None,
    ) -> list[str]:
        """
        Embed `text`, run a filtered nearest-neighbour query, return
        the `text` field from each matching document's metadata.

        filter must include at minimum:
            department    — e.g. "editorial_EU"
            document_type — e.g. "persona_guideline"
        """
        [embedding] = self._embed([text])
        results = self._get_index().query(
            vector=embedding,
            filter=filter,
            top_k=top_k or self._top_k,
            include_metadata=True,
        )
        return [match.metadata.get("text", "") for match in results.matches]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_index(self) -> Any:
        if self._index is None:
            self._index = self._pc.Index(self._index_name)
        return self._index
