"""Title normalization: drop requisition IDs, gender markers, employment-type and location noise."""

import re
from collections.abc import Callable

from jobmatch.config import NormalizationRules
from jobmatch.normalize.text import clean_text

_SEGMENT_SEPARATOR = re.compile(r"\s+[-–—|]\s+|\s*\|\s*")
_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
_EDGE_PUNCTUATION = re.compile(r"^[\s\-–—|/,:;.]+|[\s\-–—|/,:;]+$")
_SPACES = re.compile(r"\s+")


class TitleNormalizer:
    def __init__(
        self, rules: NormalizationRules, looks_like_location: Callable[[str], bool]
    ) -> None:
        self._noise = [re.compile(p, re.IGNORECASE) for p in rules.title_noise_patterns]
        self._looks_like_location = looks_like_location

    def normalize(self, title: str) -> str | None:
        """Casefolded title without noise, e.g. "senior python data engineer"."""
        text = clean_text(title).replace("\n", " ")
        for pattern in self._noise:
            text = pattern.sub(" ", text)
        segments = [s for s in _SEGMENT_SEPARATOR.split(text) if s.strip()]
        while len(segments) > 1 and self._looks_like_location(segments[-1]):
            segments.pop()
        text = " - ".join(segments)
        text = _EMPTY_BRACKETS.sub(" ", text)
        text = _SPACES.sub(" ", _EDGE_PUNCTUATION.sub("", text)).strip()
        return text.casefold() or None
