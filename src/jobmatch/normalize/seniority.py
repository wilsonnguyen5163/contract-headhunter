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
        found = self.levels(title)
        return found[-1] if found else None

    def levels(self, text: str | None) -> list[str]:
        """Every level named in the text, lowest first ("Senior / Director" gives both)."""
        found = set(self._matcher.values(text)) if text else set()
        return sorted(found, key=LEVELS.index)
