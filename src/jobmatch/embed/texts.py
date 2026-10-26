"""The text embedded for each job and each contractor.

Both sides use the same labels in the same order, so the model compares like with like:

    Title:          the role
    Target titles:  (contractors) the roles they want, Primary Role first
    Seniority:
    Skills:         taxonomy skills found in the text (both sides), then (contractors) their
                    core strengths
    Industries:     (contractors)
    Responsibilities / Requirements / Nice to have:   (jobs)
    Summary / Proof points:                           (contractors)

Left out on purpose: names, company, location, pay or rate, hours, contract type and work
mode. Those are filters and gates in matching, not meaning, and names are personal data.
Lines are ordered most useful first, because the model cuts the text at max_seq_length.
Changing a template changes TEMPLATE_HASH, which triggers a full re-embed.
"""

import hashlib

from jobmatch.normalize.skills import SkillExtractor
from jobmatch.schemas import ContractorProfile, Job

TEMPLATE_VERSION = "2026-10-22.v1"  # bump when the templates below change
TEMPLATE_HASH = hashlib.sha256(TEMPLATE_VERSION.encode("utf-8")).hexdigest()[:16]
_SEPARATOR = "; "


def _line(label: str, value: str | list[str] | None) -> str | None:
    if isinstance(value, list):
        value = _SEPARATOR.join(v for v in value if v)
    value = (value or "").strip()
    return f"{label}: {value}" if value else None


def _join(lines: list[str | None]) -> str:
    return "\n".join(line for line in lines if line)


def job_text(job: Job) -> str:
    """Title, seniority and skills, then the posting's own lists. A posting without lists
    falls back to its clean description (minus the title line already given)."""
    lists = [
        _line("Responsibilities", job.responsibilities),
        _line("Requirements", job.requirements),
        _line("Nice to have", job.nice_to_have),
    ]
    body: list[str | None] = lists
    if not any(lists) and job.description_clean:
        description = job.description_clean.split("\n", 1)
        rest = description[1] if len(description) > 1 else description[0]
        body = [_line("Description", " ".join(rest.split()))]
    return _join(
        [
            _line("Title", job.title_norm or job.title_raw),
            _line("Seniority", job.seniority),
            _line("Skills", job.skills),
            *body,
        ]
    )


def contractor_skills(profile: ContractorProfile, extractor: SkillExtractor) -> list[str]:
    """Taxonomy skills found anywhere in the profile, so both sides share one vocabulary."""
    text = "\n".join(
        part
        for part in (
            profile.standardized_headline,
            *profile.target_titles,
            *profile.core_strengths,
            profile.executive_summary,
            *profile.proof_points,
        )
        if part
    )
    return extractor.extract(text)


def contractor_text(profile: ContractorProfile, skills: list[str]) -> str:
    known = {skill.casefold() for skill in skills}
    strengths = [s for s in profile.core_strengths if s.casefold() not in known]
    return _join(
        [
            _line("Title", profile.standardized_headline),
            _line("Target titles", profile.target_titles),
            _line("Seniority", profile.seniority),
            _line("Skills", [*skills, *strengths]),
            _line("Industries", profile.main_industry),
            _line("Summary", profile.executive_summary),
            _line("Proof points", profile.proof_points),
        ]
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
