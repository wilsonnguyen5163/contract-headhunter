"""Seniority from the title (EN/FR words); the highest level found wins."""

from jobmatch.config import SeniorityLevels
from jobmatch.normalize.phrases import PhraseMatcher

LEVELS = ("junior", "intermediate", "senior", "lead", "executive")


class SeniorityDetector:
    def __init__(self, levels: SeniorityLevels) -> None:
        self._matcher = PhraseMatcher(
            {word: level for level in LEVELS for word in getattr(levels, level)}
        )

    def detect(self, title: str | None) -> str | None:
        found = self._matcher.values(title) if title else []
        return max(found, key=LEVELS.index) if found else None
