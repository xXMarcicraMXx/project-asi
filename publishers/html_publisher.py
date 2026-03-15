"""
HtmlPublisher — Metis v2 static HTML renderer.

Responsibilities:
  - Select Jinja2 template by layout_config.grid_type
  - Inject LayoutConfig CSS variables via build_template_context()
  - Atomic write: render to .tmp → os.replace(final) — no partial HTML on disk
  - Rollback backup: copy current index.html to last-good/ before overwrite
  - Write: /site/{region}/index.html  (current edition)
  - Write: /site/{region}/{date}/index.html  (immutable archive copy)
  - Regenerate: /site/{region}/archive.html  (listing with color dots)

Error contract (raise — let the pipeline send Slack and mark edition failed):
  PublishError  — TemplateNotFound
  DiskFullError — OSError with errno ENOSPC (subclass of PublishError)

Neither error class is caught internally — callers handle alerting.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import TemplateNotFound

from publishers.jinja_env import TEMPLATES_DIR, build_jinja_env, build_template_context

if TYPE_CHECKING:
    from orchestrator.brief_job_model import RegionalEdition

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_SITE_ROOT = REPO_ROOT / "site"


# ── Exceptions ────────────────────────────────────────────────────────────────

class PublishError(RuntimeError):
    """Raised when a template is missing or an unrecoverable publish error occurs."""


class DiskFullError(PublishError):
    """Raised when the disk has no space left (errno ENOSPC)."""


# ── Publisher ─────────────────────────────────────────────────────────────────

class HtmlPublisher:
    """
    Renders a RegionalEdition to static HTML and writes it to the site directory.

    Usage:
        publisher = HtmlPublisher()
        current_path, archive_path = publisher.publish(edition, run_date)
    """

    def __init__(self, site_root: Path | None = None) -> None:
        self._site_root = site_root or DEFAULT_SITE_ROOT
        self._env = build_jinja_env()

    def publish(
        self,
        edition: "RegionalEdition",
        run_date: date,
    ) -> tuple[Path, Path]:
        """
        Render and write one regional edition.

        Steps:
          1. Load template for grid_type
          2. Render HTML with CSS variables + stories
          3. Backup existing index.html to last-good/
          4. Atomic write: {region}/index.html
          5. Atomic write: {region}/{date}/index.html (archive copy)
          6. Regenerate {region}/archive.html

        Returns:
            (current_path, archive_path)

        Raises:
            PublishError  — TemplateNotFound
            DiskFullError — disk full (errno ENOSPC)
        """
        region = edition.region
        layout = edition.layout

        # ── Step 1: Load template ──────────────────────────────────────────
        try:
            tmpl = self._env.get_template(f"{layout.grid_type}.html")
        except TemplateNotFound as exc:
            raise PublishError(
                f"Metis: template '{layout.grid_type}.html' missing. "
                f"Deploy blocked for {region}."
            ) from exc

        # ── Step 2: Render HTML ────────────────────────────────────────────
        ctx = build_template_context(edition, run_date)
        html = tmpl.render(**ctx)

        # ── Step 3-4: Write current edition ───────────────────────────────
        region_dir = self._site_root / region
        region_dir.mkdir(parents=True, exist_ok=True)
        current_path = region_dir / "index.html"

        self._atomic_write(current_path, html)
        logger.info(
            "html_publisher_wrote_current",
            extra={"region": region, "path": str(current_path)},
        )

        # ── Step 5: Write archive copy ────────────────────────────────────
        archive_date_dir = region_dir / run_date.isoformat()
        archive_date_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_date_dir / "index.html"

        self._atomic_write(archive_path, html)
        logger.info(
            "html_publisher_wrote_archive",
            extra={"region": region, "date": run_date.isoformat(), "path": str(archive_path)},
        )

        # ── Step 6: Regenerate archive listing ────────────────────────────
        self._write_archive_listing(region, region_dir)

        return current_path, archive_path

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _atomic_write(self, final_path: Path, content: str) -> None:
        """
        Write `content` to `final_path` atomically.

        Protocol:
          1. If final_path exists: copy to last-good/ (non-fatal if backup fails)
          2. Write content to final_path.tmp
          3. os.replace(tmp, final_path)  ← atomic on same filesystem

        Raises:
            DiskFullError if errno is ENOSPC (tmp is cleaned up before raising)
        """
        tmp = final_path.with_suffix(".tmp")

        # Backup existing file (best-effort — never block publish on backup failure)
        if final_path.exists():
            backup_dir = final_path.parent / "last-good"
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(final_path, backup_dir / final_path.name)
            except OSError as exc:
                logger.warning(
                    "html_publisher_backup_failed",
                    extra={"path": str(final_path), "error": str(exc)},
                )

        # Write to .tmp
        try:
            tmp.write_text(content, encoding="utf-8")
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise DiskFullError(
                    f"Metis: disk full writing {final_path}. Publish halted."
                ) from exc
            raise

        # Atomic rename
        os.replace(tmp, final_path)

    def _write_archive_listing(self, region: str, region_dir: Path) -> None:
        """
        Scan region_dir for date subdirectories and regenerate archive.html.

        Each date directory is expected to be named YYYY-MM-DD.
        The daily_color is extracted from the rendered index.html inside each folder.
        Silently skips (with a warning log) if archive.html template is missing.
        """
        entries: list[dict] = []

        for d in sorted(region_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            try:
                entry_date = date.fromisoformat(d.name)
            except ValueError:
                continue  # skip non-date dirs (last-good, etc.)

            color = _extract_color_from_html(d / "index.html")
            entries.append({"date": entry_date, "color": color})

        try:
            archive_tmpl = self._env.get_template("archive.html")
        except TemplateNotFound:
            logger.warning(
                "html_publisher_archive_template_missing",
                extra={"region": region},
            )
            return

        archive_html = archive_tmpl.render(
            region=region,
            region_display=region.upper(),
            entries=entries,
        )

        archive_path = region_dir / "archive.html"
        self._atomic_write(archive_path, archive_html)
        logger.info(
            "html_publisher_wrote_archive_listing",
            extra={"region": region, "entries": len(entries)},
        )


# ── Module-level helpers ──────────────────────────────────────────────────────

def _extract_color_from_html(path: Path) -> str:
    """
    Read daily_color from a rendered edition HTML file.

    Scans for the color-dot CSS class injected by templates.
    Returns "Amber" as a safe default if the file is missing or unreadable.
    """
    if not path.exists():
        return "Amber"
    try:
        # Only read first 2 KB — color dot is always in the masthead (near top)
        with path.open(encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
        for color in ("Red", "Green", "Amber"):  # check Red/Green first (Amber is default)
            if f"color-dot--{color}" in head:
                return color
    except OSError:
        pass
    return "Amber"
