"""Skill extraction: whole-word matching of taxonomy terms and synonyms (EN/FR)."""

from jobmatch.config import SkillsTaxonomy
from jobmatch.normalize.phrases import PhraseMatcher


class SkillExtractor:
    def __init__(self, taxonomy: SkillsTaxonomy) -> None:
        terms: dict[str, str] = {}
        for canonical, entry in taxonomy.skills.items():
            for term in (canonical, *entry.aliases):
                terms[term] = canonical
        self._matcher = PhraseMatcher(terms)

    def extract(self, text: str | None) -> list[str]:
        """Canonical skills found, in order of first appearance."""
        return self._matcher.values(text) if text else []
