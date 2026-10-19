"""English / French / bilingual detection from common function words (no model, no download)."""

import re

from jobmatch.config import BilingualList
from jobmatch.normalize.phrases import fold
from jobmatch.schemas import Language

_WORD = re.compile(r"[^\W\d_]+")


class LanguageDetector:
    def __init__(self, words: BilingualList, min_hits: int, bilingual_min_share: float) -> None:
        self._en = {fold(w) for w in words.en}
        self._fr = {fold(w) for w in words.fr}
        self._min_hits = min_hits
        self._bilingual_min_share = bilingual_min_share

    def detect(self, text: str | None) -> Language | None:
        """None when there is too little English or French text to tell."""
        if not text:
            return None
        tokens = _WORD.findall(fold(text))
        en = sum(t in self._en for t in tokens)
        fr = sum(t in self._fr for t in tokens)
        total = en + fr
        if total < self._min_hits:
            return None
        if min(en, fr) / total >= self._bilingual_min_share:
            return Language.BILINGUAL
        return Language.EN if en > fr else Language.FR

    @staticmethod
    def word_count(text: str | None) -> int:
        return len(_WORD.findall(text)) if text else 0
