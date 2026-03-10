"""Abstract Publisher interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BasePublisher(ABC):
    @abstractmethod
    def publish(self, body: str, job_id: str, region_id: str, output_dir: Path) -> Path:
        """Write the article and return the path to the output file."""
