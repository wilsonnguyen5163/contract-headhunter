"""Minimum years of experience from requirement lines (EN/FR).

"5+ years of Python", "minimum of 5 years", "3-5 years" (-> 3), "8 ans d'expérience",
"au moins 2 années", "a minimum of seven (7) years'", "sept ans". The first line that
states a number of years wins. Read from every requirement line, not just the bullets
kept in Job.requirements, so a trailing "The role requires seven years..." still counts.
"""

import re
from collections.abc import Iterable, Mapping

from jobmatch.normalize.numbers import count_pattern, count_value, folded_words
from jobmatch.normalize.phrases import fold


class ExperienceReader:
    def __init__(self, number_words: Mapping[str, int] | None = None) -> None:
        self._words = folded_words(number_words or {})
        count = count_pattern(self._words)
        self._years = re.compile(
            rf"{count}(?:\s*(?:-|–|to|a|ou|or)\s*\(?[\w-]+\)?)?\s*\+?\s*"
            r"(?:years?|yrs?|annees?|ans?)\b(?!\s*(?:hour|heure))"  # not "$45 an hour"
        )

    def years_min(self, lines: Iterable[str]) -> float | None:
        for line in lines:
            match = self._years.search(fold(line))
            if match:
                return count_value(match.group("n"), self._words)
        return None


def years_experience_min(
    lines: Iterable[str], number_words: Mapping[str, int] | None = None
) -> float | None:
    return ExperienceReader(number_words).years_min(lines)
