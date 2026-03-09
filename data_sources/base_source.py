"""
Abstract base for all data sources.

A DataSource takes a topic string and returns a list of Article objects
with full body text. Concrete implementations (RSS, manual, future APIs)
all satisfy this interface so the pipeline stays source-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchestrator.job_model import Article


class BaseSource(ABC):

    @abstractmethod
    async def fetch(self, topic: str) -> list[Article]:
        """
        Fetch articles relevant to the given topic.
        Must return at least one Article with a non-empty body.
        """
        ...
