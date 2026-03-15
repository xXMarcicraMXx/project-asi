"""
Config system for Project ASI.

All configuration is YAML-driven. Pydantic validates every file on load —
bad config raises immediately, never silently.

Usage:
    from config import load_all
    configs = load_all()

    from config import load_settings, load_content_type, load_region
    settings = load_settings()
    ct = load_content_type("journal_article")
    region = load_region("EU")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Pydantic config models
# ---------------------------------------------------------------------------

class ModelsConfig(BaseModel):
    writer: str
    parser: str


class PineconeConfig(BaseModel):
    index_name: str
    top_k: int = Field(..., ge=1, le=20)
    embedding_model: str = "llama-text-embed-v2"
    embedding_dimension: int = Field(1024, gt=0)


class CostConfig(BaseModel):
    max_usd_per_job: float = Field(..., gt=0)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"

    @field_validator("level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log level must be one of {allowed}")
        return v.upper()


class SchedulerConfig(BaseModel):
    cron: str
    default_regions: list[str] = Field(..., min_length=1)
    default_topics: list[str] = Field(..., min_length=1)


class SettingsConfig(BaseModel):
    models: ModelsConfig
    pinecone: PineconeConfig
    cost: CostConfig
    logging: LoggingConfig
    scheduler: SchedulerConfig


class OutputConfig(BaseModel):
    format: str = "markdown"
    min_words: int = Field(..., gt=0)
    max_words: int = Field(..., gt=0)

    @model_validator(mode="after")
    def max_gt_min(self) -> "OutputConfig":
        if self.max_words <= self.min_words:
            raise ValueError("max_words must be greater than min_words")
        return self


class ContentTypeConfig(BaseModel):
    content_type: str
    output: OutputConfig
    agent_chain: list[str] = Field(..., min_length=1)
    writer_instructions: str
    editor_criteria: list[str] = Field(..., min_length=1)
    pinecone_filter: dict[str, str]


class DemographicAnchor(BaseModel):
    location: str
    cultural_lens: str


class PineconeMetadata(BaseModel):
    department: str


class RegionConfig(BaseModel):
    region_id: str
    display_name: str
    editorial_voice: str
    demographic_anchor: DemographicAnchor
    pinecone_metadata: PineconeMetadata
    # Metis v2: injected into CurationAgent user message (not system prompt)
    curation_bias: str | None = None


class AllConfigs(BaseModel):
    settings: SettingsConfig
    content_types: dict[str, ContentTypeConfig]
    regions: dict[str, RegionConfig]


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def load_settings() -> SettingsConfig:
    """Load and validate config/settings.yaml."""
    path = _CONFIG_DIR / "settings.yaml"
    return SettingsConfig.model_validate(_load_yaml(path))


def load_content_type(name: str) -> ContentTypeConfig:
    """
    Load and validate a content type config by name.
    e.g. load_content_type("journal_article")
    """
    path = _CONFIG_DIR / "content_types" / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No content type config found at {path}")
    return ContentTypeConfig.model_validate(_load_yaml(path))


def load_region(region_id: str) -> RegionConfig:
    """
    Load a region config by region_id (case-insensitive match on filename).
    Searches config/regions/ for a file whose region_id field matches.
    """
    regions_dir = _CONFIG_DIR / "regions"
    for yaml_file in regions_dir.glob("*.yaml"):
        data = _load_yaml(yaml_file)
        if data.get("region_id", "").upper() == region_id.upper():
            return RegionConfig.model_validate(data)
    raise FileNotFoundError(
        f"No region config found for region_id '{region_id}' in {regions_dir}"
    )


def load_all() -> AllConfigs:
    """
    Load and validate every config file.
    Raises on the first validation error — fail fast.
    """
    settings = load_settings()

    content_types: dict[str, ContentTypeConfig] = {}
    ct_dir = _CONFIG_DIR / "content_types"
    for yaml_file in sorted(ct_dir.glob("*.yaml")):
        ct = ContentTypeConfig.model_validate(_load_yaml(yaml_file))
        content_types[ct.content_type] = ct

    regions: dict[str, RegionConfig] = {}
    regions_dir = _CONFIG_DIR / "regions"
    for yaml_file in sorted(regions_dir.glob("*.yaml")):
        region = RegionConfig.model_validate(_load_yaml(yaml_file))
        regions[region.region_id] = region

    return AllConfigs(settings=settings, content_types=content_types, regions=regions)
