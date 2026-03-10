"""
MarkdownPublisher — writes approved articles to /output/{job_id}/{region}.md

Called by the Slack approval webhook handler when an article is approved.
Safe to call multiple times (overwrites existing file).
"""

from __future__ import annotations

import logging
from pathlib import Path

from publishers.base_publisher import BasePublisher

logger = logging.getLogger(__name__)


class MarkdownPublisher(BasePublisher):
    def publish(
        self,
        body: str,
        job_id: str,
        region_id: str,
        output_dir: Path,
    ) -> Path:
        """
        Write article markdown to output_dir / job_id / region_id.md.
        Creates parent directories if needed.
        Returns the path written.
        """
        out_dir = output_dir / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / f"{region_id.lower()}.md"
        out_path.write_text(body, encoding="utf-8")

        logger.info(
            "article_published",
            extra={
                "job_id": job_id,
                "region": region_id,
                "path": str(out_path),
                "bytes": len(body.encode()),
            },
        )
        return out_path
