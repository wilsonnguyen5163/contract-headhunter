"""Checks that every evidence quote in an answer appears in the text that was sent
(CLAUDE.md: explanations come from data, never invented).

A job quote must be in the job text; a contractor quote must be in that contractor's own
card. Comparison ignores case, accents are kept, curly quotes and dashes count as plain ones,
whitespace is collapsed and edge punctuation dropped. A quote with "..." or "…" is checked
piece by piece. Quotes not found are removed and reported; the match stays for the reviewer
with an "unverified evidence" note.
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
    output: JudgeOutput  # with unverified quotes removed
    unverified: dict[str, list[str]]  # alias -> quotes removed


def verify_evidence(output: JudgeOutput, job_text: str, roster: Roster) -> EvidenceReport:
    job = normalize(job_text)
    cards = {c.alias: normalize(render_card(c)) for c in roster.cards}
    cleaned = output.model_copy(deep=True)
    unverified: dict[str, list[str]] = {}
    for match in cleaned.top:
        kept = []
        for item in match.evidence:
            source = job if item.source == "job" else cards.get(match.alias, "")
            if quote_found(item.quote, source):
                kept.append(item)
            else:
                unverified.setdefault(match.alias, []).append(item.quote)
        match.evidence = kept
    return EvidenceReport(output=cleaned, unverified=unverified)
