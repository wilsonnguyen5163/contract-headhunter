"""Type of work and industry, for the field part of the score and two information notes.

Type of work comes from skill categories (marketing, sales, software, finance...): a job's is
the categories of its skill tags, counted; a contractor's the categories of their skills.
Categories used across every type of work (management, business) are ignored, as are
learned skills, which have no real category.

Industry (sector) is only ever a note: a job's is found in its title, company and cleaned
description, a contractor's in their main_industry list, through config/industries.yaml.
"""

from collections import Counter
from collections.abc import Iterable

from jobmatch.config import Industries, SkillsTaxonomy
from jobmatch.normalize.phrases import PhraseMatcher

MAX_JOB_INDUSTRIES = 2  # the most-mentioned ones; more is usually noise from long text


def skill_categories(
    skills: Iterable[str], taxonomy: SkillsTaxonomy, ignore: Iterable[str]
) -> Counter[str]:
    skip = set(ignore)
    counts: Counter[str] = Counter()
    for skill in skills:
        entry = taxonomy.skills.get(skill)
        if entry and entry.category and entry.category not in skip:
            counts[entry.category] += 1
    return counts


def field_overlap(job: Counter[str], contractor: frozenset[str]) -> float | None:
    """Share of the job's categorized skills whose category the contractor also has.
    None when either side has nothing to compare."""
    total = sum(job.values())
    if not total or not contractor:
        return None
    return sum(n for category, n in job.items() if category in contractor) / total


def main_field(job: Counter[str]) -> str | None:
    if not job:
        return None
    return min(job, key=lambda c: (-job[c], c))


def field_note(job: Counter[str], contractor: frozenset[str]) -> str | None:
    main = main_field(job)
    if main is None or not contractor or main in contractor:
        return None
    return f"field: job {main} · contractor {', '.join(sorted(contractor))}"


class IndustryFinder:
    def __init__(self, industries: Industries) -> None:
        # Job text: aliases only. A name like "education" also appears in requirements
        # ("Minimum education: Bachelor's degree").
        self._job = PhraseMatcher(
            {a: name for name, e in industries.industries.items() for a in e.aliases}
        )
        self._contractor = PhraseMatcher(
            {
                a: name
                for name, e in industries.industries.items()
                for a in (name, *e.aliases, *e.contractor_aliases)
            }
        )

    def in_job(self, text: str) -> tuple[str, ...]:
        """The most-mentioned industries in a job's text, at most MAX_JOB_INDUSTRIES."""
        counts = Counter(m.value for m in self._job.find(text))
        ranked = sorted(counts, key=lambda name: (-counts[name], name))
        return tuple(ranked[:MAX_JOB_INDUSTRIES])

    def in_contractor(self, industries: Iterable[str]) -> tuple[str, ...]:
        found: dict[str, None] = {}
        for label in industries:
            for name in self._contractor.values(label):
                found[name] = None
        return tuple(found)


def industry_note(job: tuple[str, ...], contractor: tuple[str, ...]) -> str | None:
    if not job:
        return None
    shared = [name for name in job if name in contractor]
    if shared:
        return f"same industry: {', '.join(shared)}"
    if not contractor:
        return None
    return f"industry: job {', '.join(job)} · contractor {', '.join(contractor)}"
