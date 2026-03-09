"""
SmokeWriterAgent — minimal concrete agent for the Day 5 smoke test.

Uses claude-sonnet for a single write pass with no research or edit loop.
Replaced in Sprint 2 by the full WriterAgent with ResearchAgent preprocessing
and the EditorAgent revision loop.
"""

from __future__ import annotations

from agents.base_agent import BaseAgent


class SmokeWriterAgent(BaseAgent):
    AGENT_NAME = "smoke_writer"
    MODEL = "claude-sonnet-4-20250514"
    SYSTEM_PROMPT = (
        "You are a professional journalist. "
        "Write formal, well-structured articles in markdown based on the "
        "provided sources and editorial voice. "
        "Output raw markdown only — no preamble, no sign-off. "
        "Do not fabricate statistics, quotes, or sources."
    )
