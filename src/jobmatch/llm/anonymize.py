"""Contractor cards sent to the API: what matters for matching, no personal details
(CLAUDE.md v1 rule 2; doc/V1_PLAN.md section 8).

Sent (user decision 2026-11-08: "send all contractor's details except for name"): headline,
professions, industries, seniority, years of experience, languages, target titles, core
strengths, proof points, search keywords, location (city/province), rate expectation,
weekly hours and the summary (up to llm.payload.summary_max_words words). Proof points give
the model concrete results to cite. Before 2026-11-08 location, rate, hours and proof points
were held back.

Never sent: the name and the talent ID. Each contractor gets an alias (C01, C02… in
talent-ID order); the alias-to-talent-ID map stays on this machine.

Every sent text is scrubbed: the contractor's own name (each part, accent-insensitive),
emails, URLs, phone numbers, street addresses and postal codes. If anything that still looks
like contact data is left after that, the contractor is left out of the call and reported.

Known limit: a name part that is also a common word ("Grant", "Rose") is removed wherever it
appears, which can cost a skill word. Privacy wins.
"""

import re
import unicodedata
from collections.abc import Iterable

from pydantic import BaseModel

from jobmatch.config import LlmPayloadSettings
from jobmatch.llm.payload import content_hash, neutralize
from jobmatch.schemas import ContractorProfile

NAME = "[name]"
_WORD = re.compile(r"\w+", re.UNICODE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_URL = re.compile(
    r"(?:https?://|www\.)\S+"  # with a scheme or www
    r"|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/\S*",  # bare domain with a path: linkedin.com/in/x
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_POSTAL = re.compile(
    r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b", re.IGNORECASE
)
# English: 123 Main Street, 45 King St. Case-sensitive (capitalized words and street type),
# so "20 projects the same way" is left alone.
_STREET_EN = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w'’-]*\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|"
    r"Drive|Dr|Crescent|Cres|Lane|Ln|Court|Ct|Way|Place|Pl|Trail|Highway|Hwy)\b\.?"
)
# French: 123, rue Sainte-Catherine; 45 boul. René-Lévesque
_STREET_FR = re.compile(
    r"\b\d{1,5},?\s+(?:rue|avenue|av\.|boulevard|boul\.|chemin|ch\.)\s+[\w'’.-]+"
    r"(?:\s+[A-ZÀ-Ý][\w'’.-]*){0,3}",
    re.IGNORECASE,
)
# Checked after scrubbing: still looks like contact data -> the contractor is not sent.
_LEFTOVER = re.compile(r"@\w|\d{7,}|\b(?:https?|www)\b", re.IGNORECASE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def name_parts(full_name: str) -> set[str]:
    """Folded name parts of two letters or more ("Jean-Luc O'Neil" -> jean, luc, o, neil
    minus the one-letter "o")."""
    return {_fold(p) for p in _WORD.findall(full_name) if len(p) >= 2}


def scrub(text: str, names: set[str]) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _STREET_EN.sub("[address]", text)
    text = _STREET_FR.sub("[address]", text)
    text = _POSTAL.sub("[postal code]", text)
    text = _PHONE.sub("[phone]", text)
    if names:
        text = _WORD.sub(lambda m: NAME if _fold(m.group(0)) in names else m.group(0), text)
    return text


def leftover_contact(text: str) -> bool:
    return bool(_LEFTOVER.search(text))


class ContractorCard(BaseModel):
    alias: str
    headline: str | None = None
    primary_profession: str | None = None
    secondary_professions: list[str] = []
    industries: list[str] = []
    seniority: str | None = None
    years_experience: float | None = None
    languages: list[str] = []
    target_titles: list[str] = []
    skills: list[str] = []
    proof_points: list[str] = []
    keywords: list[str] = []
    location: str | None = None
    rate: str | None = None
    weekly_hours: float | None = None
    summary: str | None = None


class Exclusion(BaseModel):
    talent_id: str
    reason: str


class Roster(BaseModel):
    cards: list[ContractorCard]
    aliases: dict[str, str]  # alias -> talent_id; local only, never sent
    excluded: list[Exclusion] = []

    def text(self) -> str:
        return render_roster(self.cards)

    def hash(self) -> str:
        return content_hash(self.text())

    def subset(self, aliases: Iterable[str]) -> "Roster":
        keep = set(aliases)
        return Roster(
            cards=[c for c in self.cards if c.alias in keep],
            aliases={a: t for a, t in self.aliases.items() if a in keep},
            excluded=self.excluded,
        )


def _words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit]) + (" …" if len(words) > limit else "")


def contractor_card(
    profile: ContractorProfile, alias: str, settings: LlmPayloadSettings
) -> ContractorCard:
    names = name_parts(profile.talent_name)
    cap = settings.max_list_items

    def one(value: str | None) -> str | None:
        return neutralize(scrub(value, names)) or None if value else None

    def many(values: list[str]) -> list[str]:
        kept: dict[str, str] = {}  # casefolded -> first spelling
        for value in values:
            cleaned = neutralize(scrub(value, names))
            if cleaned and cleaned != NAME:
                kept.setdefault(cleaned.casefold(), cleaned)
        return list(kept.values())[:cap]

    summary = one(profile.executive_summary)
    return ContractorCard(
        alias=alias,
        headline=one(profile.standardized_headline),
        primary_profession=one(profile.primary_profession),
        secondary_professions=many(profile.secondary_professions),
        industries=many(profile.main_industry),
        seniority=one(profile.seniority),
        years_experience=profile.years_experience,
        languages=profile.languages,
        target_titles=many(profile.target_titles),
        skills=many(profile.core_strengths),
        proof_points=many(profile.proof_points),
        keywords=many(profile.search_keywords),
        location=one(profile.location),
        rate=one(profile.rate_expectation),
        weekly_hours=profile.weekly_hours,
        summary=_words(summary, settings.summary_max_words) if summary else None,
    )


def render_card(card: ContractorCard) -> str:
    lines = [f"[{card.alias}]"]

    def add(label: str, value: str | list[str] | None) -> None:
        if isinstance(value, list):
            value = "; ".join(value)
        if value:
            lines.append(f"{label}: {value}")

    add("Headline", card.headline)
    add("Profession", card.primary_profession)
    add("Other professions", card.secondary_professions)
    level = card.seniority or ""
    if card.years_experience is not None:
        level = f"{level}; {card.years_experience:g} years".lstrip("; ")
    add("Level", level)
    add("Target titles", card.target_titles)
    add("Skills", card.skills)
    add("Proof points", card.proof_points)
    add("Keywords", card.keywords)
    add("Industries", card.industries)
    add("Languages", card.languages)
    add("Location", card.location)
    add("Rate expectation", card.rate)
    if card.weekly_hours is not None:
        add("Weekly hours available", f"{card.weekly_hours:g}")
    add("Summary", card.summary)
    return "\n".join(lines)


def render_roster(cards: Iterable[ContractorCard]) -> str:
    return "\n\n".join(render_card(c) for c in cards)


def build_roster(profiles: Iterable[ContractorProfile], settings: LlmPayloadSettings) -> Roster:
    ordered = sorted(profiles, key=lambda p: p.talent_id)
    width = max(2, len(str(len(ordered))))
    cards: list[ContractorCard] = []
    aliases: dict[str, str] = {}
    excluded: list[Exclusion] = []
    for profile in ordered:
        if not profile.contract:
            excluded.append(Exclusion(talent_id=profile.talent_id, reason="not contract"))
            continue
        alias = f"C{len(cards) + 1:0{width}d}"
        card = contractor_card(profile, alias, settings)
        if leftover_contact(render_card(card)):
            excluded.append(
                Exclusion(talent_id=profile.talent_id, reason="contact data left after scrubbing")
            )
            continue
        cards.append(card)
        aliases[alias] = profile.talent_id
    return Roster(cards=cards, aliases=aliases, excluded=excluded)
