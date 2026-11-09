"""Checks that what a model claims comes from the text that was sent (CLAUDE.md: explanations
come from data, never invented).

v10: each row of a deep review's "meets" table carries two copied texts. "skill" must be in
that contractor's profile as sent, and "requirement_quote" must be in the job text. Only a
row where both are found is kept, and only then is its short summary ("requirement") shown
next to the skill. A row that fails is removed and reported as "meets: <skill>"; the match
stays for the reviewer with a note that something could not be confirmed.

Comparison ignores case, accents are kept, curly quotes and dashes count as plain ones,
whitespace is collapsed and edge punctuation dropped. A quote with "..." or "…" is checked
piece by piece.
"""

import re
import unicodedata

from pydantic import BaseModel

from jobmatch.llm.anonymize import Roster, render_card
from jobmatch.llm.prompt import JudgeOutput

_SPACES = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\.\.|…")
_TRANSLATE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_EDGE = " .,;:!?\"'()[]-"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE).casefold()
    return _SPACES.sub(" ", text).strip()


def quote_found(quote: str, haystack: str) -> bool:
    """`haystack` must already be normalized."""
    pieces = [normalize(p).strip(_EDGE) for p in _ELLIPSIS.split(quote)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(p in haystack for p in pieces)


class EvidenceReport(BaseModel):
    output: JudgeOutput  # with unconfirmed rows removed
    unverified: dict[str, list[str]]  # alias -> rows removed ("meets: <skill>")


def verify_evidence(output: JudgeOutput, job_text: str, roster: Roster) -> EvidenceReport:
    job = normalize(job_text)
    cards = {c.alias: normalize(render_card(c)) for c in roster.cards}
    cleaned = output.model_copy(deep=True)
    unverified: dict[str, list[str]] = {}
    for match in cleaned.top:
        card = cards.get(match.alias, "")
        kept = []
        for row in match.meets:
            if quote_found(row.skill, card) and quote_found(row.requirement_quote, job):
                kept.append(row)
            else:
                unverified.setdefault(match.alias, []).append(f"meets: {row.skill}")
        match.meets = kept
    return EvidenceReport(output=cleaned, unverified=unverified)
