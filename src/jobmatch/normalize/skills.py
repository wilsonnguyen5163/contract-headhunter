"""Skill extraction: whole-word matching of taxonomy terms and synonyms (EN/FR)."""

from jobmatch.config import SkillsTaxonomy
from jobmatch.normalize.phrases import PhraseMatcher

_NOT_A_SKILL = ""  # value of a not_when phrase: it claims the text, then is dropped


class SkillExtractor:
    def __init__(self, taxonomy: SkillsTaxonomy) -> None:
        terms: dict[str, str] = {}
        for canonical, entry in taxonomy.skills.items():
            for term in (canonical, *entry.aliases):
                terms[term] = canonical
            for phrase in entry.not_when:
                terms[phrase] = _NOT_A_SKILL
        self._matcher = PhraseMatcher(terms)

    def extract(self, text: str | None) -> list[str]:
        """Canonical skills found, in order of first appearance. The longest phrase wins at a
        position, so "excel at building" (a not_when phrase) hides "excel"."""
        if not text:
            return []
        return [s for s in self._matcher.values(text) if s != _NOT_A_SKILL]
