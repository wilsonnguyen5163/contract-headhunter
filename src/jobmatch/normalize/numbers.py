"""Counts written as digits or words ("7", "seven", "sept", "seven (7)"), for years of
experience and contract length. Number words come from normalization.yaml."""

import re
from collections.abc import Mapping

from jobmatch.normalize.phrases import fold


def count_pattern(words: Mapping[str, int]) -> str:
    """Regex (for folded text) matching one count; the value is in the group named "n".
    "seven (7)" and "(7)" are accepted; hyphenated words ("twenty-four") too."""
    alternatives = sorted({fold(w) for w in words}, key=len, reverse=True)
    word_part = "|".join(re.escape(w).replace(r"\-", r"[\s-]") for w in alternatives)
    number = rf"\d{{1,2}}(?:[.,]\d)?|{word_part}" if word_part else r"\d{1,2}(?:[.,]\d)?"
    return rf"(?<![\w.,])\(?(?P<n>{number})\)?(?:\s*\(\s*\d{{1,2}}\s*\))?"


def count_value(token: str, words: Mapping[str, int]) -> float:
    token = fold(token).strip()
    if token[:1].isdigit():
        return float(token.replace(",", "."))
    return float(words[re.sub(r"[\s-]+", "-", token)])


def folded_words(words: Mapping[str, int]) -> dict[str, int]:
    return {re.sub(r"[\s-]+", "-", fold(w)): n for w, n in words.items()}
