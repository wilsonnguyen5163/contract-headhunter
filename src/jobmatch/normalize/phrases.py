"""Case- and accent-insensitive whole-word phrase matching, shared by the normalizers.

"Java" never matches inside "JavaScript"; "full-stack" matches "full stack";
"Développeur" matches "developpeur". Positions refer to the folded text.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import NamedTuple

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})
_SEPARATORS = re.compile(r"[\s\-]+")


def fold(text: str) -> str:
    """Casefold, strip accents and unify apostrophes: "Développeur’s" -> "developpeur's"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_APOSTROPHES).casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _key(phrase: str) -> str:
    return _SEPARATORS.sub(" ", fold(phrase)).strip()


class PhraseMatch(NamedTuple):
    value: str
    start: int
    end: int


class PhraseMatcher:
    """Finds phrases (mapped to values) as whole words; longest phrase wins at a position."""

    def __init__(self, phrases: Mapping[str, str], *, allow_plural: bool = False) -> None:
        self._values: dict[str, str] = {}
        for phrase, value in phrases.items():
            key = _key(phrase)
            if key:
                self._values.setdefault(key, value)
        if not self._values:
            self._regex: re.Pattern[str] | None = None
            return
        alternatives = sorted(self._values, key=len, reverse=True)
        body = "|".join(r"[\s\-]+".join(re.escape(t) for t in k.split(" ")) for k in alternatives)
        plural = "s?" if allow_plural else ""
        self._regex = re.compile(rf"(?<!\w)({body}){plural}(?!\w)")

    @classmethod
    def from_terms(cls, terms: Iterable[str], *, allow_plural: bool = False) -> "PhraseMatcher":
        return cls({t: t for t in terms}, allow_plural=allow_plural)

    def find(self, text: str) -> list[PhraseMatch]:
        if self._regex is None or not text:
            return []
        return [
            PhraseMatch(self._values[_key(m.group(1))], m.start(), m.end())
            for m in self._regex.finditer(fold(text))
        ]

    def values(self, text: str) -> list[str]:
        """Matched values, unique, in order of first appearance."""
        return list(dict.fromkeys(m.value for m in self.find(text)))

    def remove(self, text: str) -> str:
        """Return the folded text with every match replaced by a space."""
        folded = fold(text)
        if self._regex is None:
            return folded
        return self._regex.sub(" ", folded)
