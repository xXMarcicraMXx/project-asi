"""
Day 4 validation script.

Calls RSSSource.fetch("interest rates") and confirms:
1. At least 3 articles returned
2. Every article has a non-empty title, url, and body
3. Every body is >= 200 characters (not just a stub)

Usage:
    python scripts/validate_day4.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from data_sources.rss_source import RSSSource


async def main() -> None:
    source = RSSSource()
    topic = "interest rates"

    print(f"Fetching articles for topic: '{topic}'...")
    articles = await source.fetch(topic)

    print(f"Found {len(articles)} article(s)\n")

    for i, a in enumerate(articles, 1):
        print(f"  [{i}] {a.title}")
        print(f"       source : {a.source_name}")
        print(f"       url    : {a.url}")
        print(f"       body   : {len(a.body)} chars")
        print()

    assert len(articles) >= 3, f"Expected >= 3 articles, got {len(articles)}"
    for a in articles:
        assert a.title, "Article missing title"
        assert a.body,  "Article missing body"
        assert len(a.body) >= 200, f"Body too short ({len(a.body)} chars): {a.title}"

    print("Day 4 validation PASSED")


if __name__ == "__main__":
    asyncio.run(main())
