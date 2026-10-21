"""Full-time / part-time commitment, independent of employment type.

Texts are checked in the order given (the source's employment-type value, the title, then
the job text); the first full- or part-time term seen wins. Without any term, a stated
"37.5 hours per week" / "20 h/semaine" decides against commitment.full_time_min_hours.
"""

import re

from jobmatch.config import CommitmentRules
from jobmatch.normalize.phrases import PhraseMatcher, fold
from jobmatch.schemas import Commitment

_HOURS_PER_WEEK = re.compile(
    r"\b(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:-\s*\d{1,2}(?:[.,]\d{1,2})?\s*)?"
    r"(?:hours?|hrs?|heures?|h)\s*(?:per|a|an|/|par|each|every)\s*(?:week|wk|semaine)\b"
)


class CommitmentDetector:
    def __init__(self, rules: CommitmentRules) -> None:
        terms = {t: Commitment.FULL_TIME.value for t in rules.full_time}
        terms |= {t: Commitment.PART_TIME.value for t in rules.part_time}
        self._matcher = PhraseMatcher(terms)
        self._full_time_min_hours = rules.full_time_min_hours

    def detect(self, *texts: str | None) -> Commitment:
        present = [t for t in texts if t]
        for text in present:
            found = self._matcher.find(text)
            if found:
                return Commitment(found[0].value)
        for text in present:
            hours = _HOURS_PER_WEEK.search(fold(text))
            if hours:
                value = float(hours.group(1).replace(",", "."))
                if value >= self._full_time_min_hours:
                    return Commitment.FULL_TIME
                return Commitment.PART_TIME
        return Commitment.UNKNOWN
