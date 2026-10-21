"""Work mode (on-site / hybrid / remote) from EN/FR wording, with one precedence rule.

When a posting states several modes:
- hybrid + remote  -> remote
- hybrid + on-site -> on-site
- remote + on-site -> whichever is seen first
Terms and patterns live in canada_locations.yaml.
"""

import re
from collections.abc import Iterable

from jobmatch.config import CanadaLocations
from jobmatch.normalize.phrases import PhraseMatcher, fold
from jobmatch.schemas import RemoteType

_EXTREMES = (RemoteType.REMOTE, RemoteType.ONSITE)


def resolve(signals: Iterable[RemoteType]) -> RemoteType:
    """Apply the precedence rule to modes in the order they were seen."""
    seen = list(signals)
    for mode in seen:
        if mode in _EXTREMES:
            return mode
    return RemoteType.HYBRID if RemoteType.HYBRID in seen else RemoteType.UNKNOWN


class WorkModeDetector:
    def __init__(self, locations: CanadaLocations) -> None:
        terms: dict[str, str] = {}
        for mode, words in (
            (RemoteType.REMOTE, locations.remote_terms),
            (RemoteType.HYBRID, locations.hybrid_terms),
            (RemoteType.ONSITE, locations.onsite_terms),
        ):
            for word in words:
                terms.setdefault(word, mode.value)
        self._matcher = PhraseMatcher(terms)
        self._patterns = [re.compile(p) for p in locations.hybrid_patterns]

    def signals(self, text: str | None, *, terms: bool = True) -> list[RemoteType]:
        """Modes mentioned in the text, in order. terms=False checks only the hybrid patterns
        ("3 days a week in the office"), which are safe to apply to long sentences."""
        if not text:
            return []
        folded = fold(text)
        found: list[tuple[int, RemoteType]] = []
        for pattern in self._patterns:
            for match in pattern.finditer(folded):
                found.append((match.start(), RemoteType.HYBRID))
                # Blank the phrase so its "in office" is not also read as on-site.
                folded = folded[: match.start()] + " " * len(match.group()) + folded[match.end() :]
        if terms:
            found.extend((m.start, RemoteType(m.value)) for m in self._matcher.find(folded))
        return [mode for _, mode in sorted(found, key=lambda item: item[0])]

    def detect(self, text: str | None) -> RemoteType:
        return resolve(self.signals(text))

    def remove(self, text: str) -> str:
        """Folded text with every work-mode term blanked (for location-only checks)."""
        return self._matcher.remove(text)
