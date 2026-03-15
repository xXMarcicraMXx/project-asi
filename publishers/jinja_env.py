"""
Jinja2 environment factory for Metis HTML templates.

Provides:
  - build_jinja_env()       → Environment (autoescape=True for .html files)
  - safe_url()              → security helper, only allows http/https URLs
  - build_template_context() → converts RegionalEdition + date into template vars

Design notes:
  - autoescape=True for .html; .css includes use {% autoescape false %} blocks
  - All CSS custom property values originate from our Python mappings or Pydantic-
    validated colors — never from raw agent text
  - safe_url() is registered as a Jinja2 global so templates call it directly
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

if TYPE_CHECKING:
    from orchestrator.brief_job_model import RegionalEdition

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# ── Design system mappings ────────────────────────────────────────────────────
# These map LayoutConfig enum values to concrete CSS primitives.
# None of these values come from agent output.

_BG_STYLE: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "text": "#1a1a1a",
        "muted": "#666666",
        "border": "#e0e0e0",
        "surface": "#f8f9fa",
    },
    "dark": {
        "bg": "#16213e",
        "text": "#e2e2e2",
        "muted": "#9090a0",
        "border": "#2a2a4a",
        "surface": "#1e2a45",
    },
    "warm-neutral": {
        "bg": "#faf7f0",
        "text": "#2d2512",
        "muted": "#7a6a50",
        "border": "#dfd8c8",
        "surface": "#f2ede3",
    },
    "cool-neutral": {
        "bg": "#f0f4f8",
        "text": "#1c2833",
        "muted": "#5a7080",
        "border": "#c8d8e0",
        "surface": "#e4edf5",
    },
}

_FONT_FAMILY: dict[str, dict[str, str]] = {
    "serif": {
        "heading": "Georgia, Times New Roman, Times, serif",
        "body": "Georgia, Times New Roman, Times, serif",
    },
    "sans": {
        "heading": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, sans-serif",
        "body": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, sans-serif",
    },
    "mixed": {
        "heading": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, sans-serif",
        "body": "Georgia, Times New Roman, Times, serif",
    },
}

_FONT_WEIGHT: dict[str, dict[str, str]] = {
    "light":   {"heading": "300", "body": "300", "label": "400"},
    "regular": {"heading": "600", "body": "400", "label": "500"},
    "heavy":   {"heading": "700", "body": "400", "label": "700"},
}

_SPACING: dict[str, dict[str, str]] = {
    "dense":    {"unit": "0.75rem", "gap": "1rem",    "section-gap": "1.5rem"},
    "balanced": {"unit": "1rem",    "gap": "1.5rem",  "section-gap": "2.5rem"},
    "airy":     {"unit": "1.25rem", "gap": "2rem",    "section-gap": "3.5rem"},
}


# ── Security helper ───────────────────────────────────────────────────────────

def safe_url(url: Optional[str]) -> str:
    """
    Return url only when the scheme is http or https.
    Any other value (javascript:, data:, None, empty) returns '#'.
    Registered as a Jinja2 global so templates call safe_url(story.url) directly.
    """
    if url and isinstance(url, str):
        stripped = url.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return stripped
    return "#"


# ── Environment factory ───────────────────────────────────────────────────────

def build_jinja_env() -> Environment:
    """
    Create the Metis Jinja2 Environment.

    - autoescape enabled for .html files only (.css includes use autoescape blocks)
    - safe_url() registered as global
    - trim_blocks + lstrip_blocks for clean whitespace in rendered HTML
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["safe_url"] = safe_url
    return env


# ── Template context builder ──────────────────────────────────────────────────

def _build_css_vars_block(css_vars: dict) -> Markup:
    """
    Render a CSS :root block from css_vars as a Markup (safe, no escaping).

    Values originate from our design-system mappings or Pydantic-validated
    hex colors — they contain no HTML-special characters.
    Returning Markup tells Jinja2 not to escape this string.
    """
    lines = [":root {"]
    for name, value in css_vars.items():
        lines.append(f"  --{name}: {value};")
    lines.append("}")
    return Markup("\n".join(lines))


def _load_base_css() -> Markup:
    """Load _base.css as Markup so Jinja2 embeds it verbatim in <style>."""
    content = (TEMPLATES_DIR / "_base.css").read_text(encoding="utf-8")
    return Markup(content)


def build_template_context(
    edition: "RegionalEdition",
    run_date: date,
) -> dict:
    """
    Convert a RegionalEdition into a flat template context dict.

    Keys injected:
      edition            — the full RegionalEdition
      layout             — shorthand for edition.layout
      run_date           — datetime.date
      region_display     — uppercase region string (e.g. "EU")
      css_vars           — dict of resolved CSS variable name → value
      all_stories        — stories sorted by rank
      stories_by_section — stories grouped by layout.section_order
    """
    layout = edition.layout

    bg      = _BG_STYLE.get(layout.background_style, _BG_STYLE["light"])
    fonts   = _FONT_FAMILY.get(layout.typography_family, _FONT_FAMILY["sans"])
    weights = _FONT_WEIGHT.get(layout.typography_weight, _FONT_WEIGHT["regular"])
    spacing = _SPACING.get(layout.visual_weight, _SPACING["balanced"])

    css_vars: dict[str, str] = {
        # Agent-generated brand colors (validated #RRGGBB — no HTML-special chars)
        "color-primary":   layout.primary_color,
        "color-secondary": layout.secondary_color,
        "color-accent":    layout.accent_color,
        # Background palette from design system mapping
        "color-bg":        bg["bg"],
        "color-text":      bg["text"],
        "color-muted":     bg["muted"],
        "color-border":    bg["border"],
        "color-surface":   bg["surface"],
        # Typography
        "font-heading":    fonts["heading"],
        "font-body":       fonts["body"],
        "fw-heading":      weights["heading"],
        "fw-body":         weights["body"],
        "fw-label":        weights["label"],
        # Spacing
        "spacing-unit":    spacing["unit"],
        "gap":             spacing["gap"],
        "section-gap":     spacing["section-gap"],
    }

    all_stories = sorted(edition.stories, key=lambda s: s.rank)

    # Group stories by section, respecting section_order
    stories_by_section: dict[str, list] = {cat: [] for cat in layout.section_order}
    for story in all_stories:
        bucket = stories_by_section.get(story.category)
        if bucket is not None:
            bucket.append(story)

    return {
        "edition":            edition,
        "layout":             layout,
        "run_date":           run_date,
        "region_display":     edition.region.upper(),
        "css_vars":           css_vars,
        "css_vars_block":     _build_css_vars_block(css_vars),
        "base_css":           _load_base_css(),
        "all_stories":        all_stories,
        "stories_by_section": stories_by_section,
    }
