"""Contract detection: the source's structured field first, then weighted EN/FR phrases.

Recall first: a posting is called permanent only on a clear signal; anything unclear stays
"unknown" and is kept for review.
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel

from jobmatch.config import ContractPhrases
from jobmatch.normalize.numbers import count_pattern, count_value, folded_words
from jobmatch.normalize.phrases import PhraseMatch, PhraseMatcher, fold
from jobmatch.schemas import EmploymentType

_WEEKS_PER_MONTH = 52 / 12
_CONTRACT_WORDS = r"(?:contract|term|mandate|contrat|mandat|ftc|cdd)"


class DurationParser:
    """Contract length in folded text: "6-month", "12 mois", "six-month mandate",
    "a one-year contract", "2-year FTC", "contrat d'un an", "8 weeks". Years only count next
    to a contract word, so "5+ years of Python" is not a duration."""

    def __init__(self, number_words: Mapping[str, int] | None = None) -> None:
        self._words = folded_words(number_words or {})
        count = count_pattern(self._words)
        self._months = re.compile(rf"{count}\s*\+?\s*-?\s*(?:months?|mois)\b")
        self._weeks = re.compile(rf"{count}\s*-?\s*(?:weeks?|semaines?)\b")
        years = r"(?:years?|ans?|annees?)"
        self._years_before = re.compile(rf"{count}\s*-?\s*{years}\s+{_CONTRACT_WORDS}\b")
        self._years_after = re.compile(
            rf"\b{_CONTRACT_WORDS}\s+(?:of|de|d'|d)\s*{count}\s*{years}\b"
        )

    def months(self, text: str) -> float | None:
        folded = fold(text)
        match = self._months.search(folded)
        if match:
            return count_value(match.group("n"), self._words)
        match = self._years_before.search(folded) or self._years_after.search(folded)
        if match:
            return 12.0 * count_value(match.group("n"), self._words)
        match = self._weeks.search(folded)
        if match:
            return round(count_value(match.group("n"), self._words) / _WEEKS_PER_MONTH, 1)
        return None


class ContractAssessment(BaseModel):
    employment_type: EmploymentType
    score: float
    signals: list[str]  # contract phrases found, as written in contract_phrases.yaml
    duration_months: float | None


def parse_duration_months(text: str, number_words: Mapping[str, int] | None = None) -> float | None:
    return DurationParser(number_words).months(text)


def _structured_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", fold(value)).strip()


class ContractDetector:
    def __init__(
        self,
        phrases: ContractPhrases,
        threshold: float,
        number_words: Mapping[str, int] | None = None,
    ) -> None:
        self._threshold = threshold
        self._durations = DurationParser(number_words)
        self._duration_weight = phrases.duration_weight
        self._contract_weights = {p.phrase: p.weight for p in phrases.contract}
        self._permanent_weights = {p.phrase: p.weight for p in phrases.permanent}
        self._contract = PhraseMatcher.from_terms(self._contract_weights, allow_plural=True)
        self._permanent = PhraseMatcher.from_terms(self._permanent_weights, allow_plural=True)
        self._false_positives = PhraseMatcher.from_terms(
            [p.phrase for p in phrases.false_positives], allow_plural=True
        )
        self._structured: dict[str, EmploymentType] = {}
        for employment_type, values in (
            (EmploymentType.CONTRACT, phrases.structured_types.contract),
            (EmploymentType.TEMPORARY, phrases.structured_types.temporary),
            (EmploymentType.PERMANENT, phrases.structured_types.permanent),
        ):
            for value in values:
                self._structured[_structured_key(value)] = employment_type

    def assess(self, text: str, structured: str | None = None) -> ContractAssessment:
        blocked = [(m.start, m.end) for m in self._false_positives.find(text)]

        def hits(matcher: PhraseMatcher) -> list[str]:
            return list(dict.fromkeys(m.value for m in matcher.find(text) if _free(m, blocked)))

        contract_hits = hits(self._contract)
        permanent_hits = hits(self._permanent)
        duration = self._durations.months(text) if contract_hits else None
        score = sum(self._contract_weights[h] for h in contract_hits)
        score -= sum(self._permanent_weights[h] for h in permanent_hits)
        if duration is not None:
            score += self._duration_weight

        employment_type = self._structured.get(_structured_key(structured)) if structured else None
        if employment_type is None:
            if score >= self._threshold:
                employment_type = EmploymentType.CONTRACT
            elif score <= -self._threshold:
                employment_type = EmploymentType.PERMANENT
            else:
                employment_type = EmploymentType.UNKNOWN
        if employment_type not in (EmploymentType.CONTRACT, EmploymentType.TEMPORARY):
            duration = None
        return ContractAssessment(
            employment_type=employment_type,
            score=round(score, 3),
            signals=contract_hits,
            duration_months=duration,
        )


def _free(match: PhraseMatch, blocked: list[tuple[int, int]]) -> bool:
    return not any(match.start < end and start < match.end for start, end in blocked)
