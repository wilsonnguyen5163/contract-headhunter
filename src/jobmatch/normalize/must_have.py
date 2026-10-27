"""Must-have skills: the skills named in a job's Requirements lines, except lines that use
softer wording ("familiarity with", "an asset", "un atout"...). Postings rarely say "must",
so requiring strong wording would leave almost no must-haves; demoting soft lines keeps the
signal and drops the obvious nice-to-haves. The phrases live in normalization.yaml."""

from jobmatch.config import BilingualList
from jobmatch.normalize.phrases import PhraseMatcher
from jobmatch.normalize.skills import SkillExtractor


class MustHaveReader:
    def __init__(self, skills: SkillExtractor, soft_markers: BilingualList) -> None:
        self._skills = skills
        self._soft = PhraseMatcher.from_terms(soft_markers.all())

    def is_soft(self, line: str) -> bool:
        return bool(self._soft.find(line))

    def read(self, requirements: list[str]) -> list[str]:
        """Canonical skills from the firm requirement lines, unique, in order of appearance."""
        found: list[str] = []
        for line in requirements:
            if not self.is_soft(line):
                found.extend(self._skills.extract(line))
        return list(dict.fromkeys(found))
