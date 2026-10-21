"""Split posting text into labeled sections: overview, responsibilities, requirements,
nice-to-have, pay, benefits, employer pitch, equal opportunity, application, other.

A line is a header when it matches a known header of a kind (optionally "Header:" with
inline text, or a trailing "?"), or when it is a short, header-like line whose folded
text matches one of the kind's patterns (normalization.yaml). Every other line stays in
the section it appears in, so each employer's own sub-headings need no configuration.
"""

import re
from enum import StrEnum

from pydantic import BaseModel

from jobmatch.config import NormalizationRules
from jobmatch.normalize.phrases import fold

_HEADER_DECORATION = " \t#*•·▪-–—=_"
_BULLET_START = re.compile(r"^\s*(?:[•·▪◦●\-*–—✓✔→>]|\(?\d{1,2}[.)])")
_BULLET = re.compile(r"^(?:[•·▪◦●\-*–—✓✔→>]+|\(?\d{1,2}[.)])\s*")
_MAX_PATTERN_HEADER_WORDS = 5
_MAX_ITEM_CHARS = 300
_MIN_SENTENCE_WORDS = 8  # a plain line this long, ending in . ! ?, closes a bulleted list
# Small words ignored when deciding whether a short line is a Title Case sub-heading.
_MINOR_WORDS = {
    "and", "&", "of", "the", "for", "to", "in", "on", "with", "a", "an", "or",
    "et", "de", "des", "du", "la", "le", "les", "en", "pour", "au", "aux", "à", "ou",
}  # fmt: skip


class SectionKind(StrEnum):
    INTRO = "intro"  # text before the first header
    OVERVIEW = "overview"
    RESPONSIBILITIES = "responsibilities"
    NICE_TO_HAVE = "nice_to_have"
    REQUIREMENTS = "requirements"
    BENEFITS = "benefits"
    PAY = "pay"
    EMPLOYER = "employer"
    EQUAL_OPPORTUNITY = "equal_opportunity"
    APPLICATION = "application"
    OTHER = "other"


class Section(BaseModel):
    kind: SectionKind
    header: str | None  # the header line as written; None for the intro
    inline: str = ""  # text after "Header:" on the header line
    lines: list[str] = []


class PostingSections(BaseModel):
    sections: list[Section]
    kept: set[SectionKind]
    pay_context: str = (
        ""  # regex of pay words (folded); lines with an amount and one are not bullets
    )

    def text(self, kinds: set[SectionKind]) -> str:
        """Text of the given kinds, headers included, in posting order."""
        parts: list[str] = []
        for section in self.sections:
            if section.kind in kinds:
                if section.header is not None:
                    parts.append(section.header)
                parts.extend(section.lines)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()

    def clean_text(self) -> str:
        """The job itself: kept kinds plus the intro, without pitch, benefits or pay."""
        return self.text(self.kept | {SectionKind.INTRO})

    def lines(self, kinds: set[SectionKind]) -> list[str]:
        return [line for line in self.text(kinds).split("\n") if line.strip()]

    def role_lines(self) -> list[str]:
        """What the role is about: intro and overview prose, without "Label: value" metadata
        ("Reports to: People Leader") or application instructions ("Please apply with...")."""
        lines: list[str] = []
        for section in self.sections:
            if section.kind in (SectionKind.INTRO, SectionKind.OVERVIEW):
                lines.extend(_prune_metadata(section.lines))
        return lines

    def items(self, kind: SectionKind, limit: int | None) -> list[str]:
        """Bullet lines of one kind, de-duplicated, at most `limit` (None = all).

        Where a section's list ends: in a bulleted list, the first plain sentence after the
        bullets (8+ words ending in . ! ?) ends it; the rest stays in the job text (signals,
        years of experience) but is not a bullet. Lowercase lines continue the bullet above;
        short plain lines between bullets are sub-headings. Lists without bullet markers
        (plain pastes) have no such signal: headers and `limit` bound them. Pay sentences,
        application instructions and recruitment-process notes are never bullets."""
        pay_context = re.compile(self.pay_context) if self.pay_context else None
        found: list[str] = []
        for section in self.sections:
            if section.kind is kind:
                found.extend(_section_items([section.inline, *section.lines], pay_context))
        unique = list(dict.fromkeys(found))
        return unique if limit is None else unique[:limit]


class SectionSplitter:
    def __init__(self, rules: NormalizationRules) -> None:
        self._kept = {SectionKind(name) for name in rules.kept_sections}
        self._headers: dict[str, SectionKind] = {}
        self._patterns: list[tuple[SectionKind, re.Pattern[str]]] = []
        for name, rule in rules.sections.items():
            kind = SectionKind(name)
            for header in rule.headers.all():
                self._headers.setdefault(_header_key(header), kind)
            self._patterns.extend((kind, re.compile(p)) for p in rule.patterns)
        terms = sorted({fold(t).strip() for t in rules.pay.context_terms if t.strip()})
        self._pay_context = (
            "|".join(rf"(?<![a-z]){re.escape(t)}(?![a-z])" for t in terms) if terms else ""
        )

    def classify(
        self, line: str, *, previous: str = "", current: SectionKind | None = None
    ) -> tuple[SectionKind, str] | None:
        """Return (kind, inline text) if the line is a header, else None."""
        stripped = line.strip(_HEADER_DECORATION)
        if not stripped:
            return None
        key = _header_key(stripped)
        if key in self._headers:
            return self._headers[key], ""
        if ":" in stripped:
            head, rest = stripped.split(":", 1)
            head_key = _header_key(head)
            if head_key in self._headers:
                return self._headers[head_key], rest.strip()
        if not self._header_like(line, stripped, previous):
            return None
        for kind, pattern in self._patterns:
            if pattern.search(key):
                # A pattern match inside a section of the same kind is just a content line
                # ("Experience with SQL Server" under Requirements).
                return None if kind is current else (kind, "")
        return None

    @staticmethod
    def _header_like(line: str, stripped: str, previous: str) -> bool:
        words = stripped.rstrip(":?").split()
        return (
            0 < len(words) <= _MAX_PATTERN_HEADER_WORDS
            and not _BULLET_START.match(line)
            and not any(c.isdigit() for c in stripped)
            and not stripped.endswith((".", ";", ","))
            and (stripped.endswith((":", "?")) or not previous.strip())
        )

    def parse(self, text: str) -> PostingSections:
        sections = [Section(kind=SectionKind.INTRO, header=None)]
        previous = ""
        for line in text.split("\n"):
            header = self.classify(line, previous=previous, current=sections[-1].kind)
            if header is None:
                sections[-1].lines.append(line)
            else:
                sections.append(Section(kind=header[0], header=line, inline=header[1]))
            previous = line
        kept = [s for s in sections if s.header is not None or any(x.strip() for x in s.lines)]
        return PostingSections(sections=kept, kept=self._kept, pay_context=self._pay_context)

    # Convenience wrappers used by the pipeline and tests.
    def strip_boilerplate(self, text: str) -> str:
        return self.parse(text).clean_text()

    def requirements(self, text: str, limit: int) -> list[str]:
        return self.parse(text).items(SectionKind.REQUIREMENTS, limit)


# Application instructions and recruitment-process notes (folded text). Specific phrases
# only: bare "apply" / "application" also appear in real duties ("apply Agile principles",
# "web application security").
_APPLY_CUE = re.compile(
    r"\b(?:(?:please |how to |to )?apply (?:now|online|today|here|with|by|via|through|at)"
    r"|how to apply|applicants are (?:invited|encouraged|asked|required to submit)"
    r"|submit (?:your|a) (?:resume|cv|portfolio|application|cover letter)"
    r"|send (?:your|a) (?:resume|cv|portfolio|application)"
    r"|(?:recruitment|hiring|selection|interview) process"
    r"|postule[rz]|soumettre (?:votre|un|une) (?:candidature|cv|portfolio)"
    r"|faire parvenir (?:votre|un) (?:cv|candidature)"
    r"|processus de (?:recrutement|selection|candidature))\b"
)
_PAY_AMOUNT = re.compile(r"(?:\$|\bcad\b|\busd\b)\s?\d|\d[\d ,.]*\s?(?:\$|k\b)")
_MAX_LABEL_WORDS = 4
_MAX_VALUE_WORDS = 8


def _prune_metadata(lines: list[str]) -> list[str]:
    kept: list[str] = []
    skip_value = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        words = line.split()
        if skip_value:
            skip_value = False
            if len(words) <= _MAX_VALUE_WORDS and not line.endswith("."):
                continue  # the value under a "Label:" line
        if line.endswith(":") and len(words) <= _MAX_LABEL_WORDS:
            skip_value = True
            continue
        if ":" in line:
            label, value = line.split(":", 1)
            if (
                0 < len(label.split()) <= _MAX_LABEL_WORDS
                and len(value.split()) <= _MAX_VALUE_WORDS
                and not value.strip().endswith(".")
            ):
                continue  # "Reports to: People Leader, Brand"
        if _APPLY_CUE.search(fold(line)):
            continue
        kept.append(_BULLET.sub("", line).strip())
    return kept


def _header_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text).strip(_HEADER_DECORATION + ":?")).strip()


def _is_sentence(line: str) -> bool:
    return len(line.split()) >= _MIN_SENTENCE_WORDS and line.endswith((".", "!", "?"))


def _is_aside(item: str, pay_context: re.Pattern[str] | None) -> bool:
    """Not a duty or requirement: an application step, a recruitment-process note, or a
    pay sentence ("The salary range is $70,000 – $80,000 CAD.")."""
    folded = fold(item)
    if _APPLY_CUE.search(folded):
        return True
    return bool(pay_context and _PAY_AMOUNT.search(folded) and pay_context.search(folded))


def _section_items(lines: list[str], pay_context: re.Pattern[str] | None) -> list[str]:
    bulleted = any(_BULLET_START.match(line) for line in lines if line.strip())
    items: list[str] = []
    started = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        is_bullet = bool(_BULLET_START.match(line))
        if bulleted and started and not is_bullet:
            if stripped[0].islower() and items:
                items[-1] = f"{items[-1]} {stripped}"[:_MAX_ITEM_CHARS]  # wrapped bullet
                continue
            if _is_sentence(stripped):
                break  # a plain sentence after the bullets: the list has ended
            continue  # a short plain line between bullets: a sub-heading
        started = started or is_bullet
        item = _BULLET.sub("", stripped).strip()
        if len(item) < 3 or item.endswith(":"):
            continue
        if not is_bullet and _is_subheading(item, lines, index):
            continue
        if _is_aside(item, pay_context):
            continue
        items.append(item[:_MAX_ITEM_CHARS])
    return items


def _is_subheading(item: str, lines: list[str], index: int) -> bool:
    """A short Title Case line followed by a blank line and more text, e.g.
    "Community Management, Automation & Reporting" inside a responsibilities list."""
    words = [w.strip(",;") for w in item.split()]
    if not 0 < len(words) <= 6 or item.endswith((".", "!", "?")):
        return False
    significant = [w for w in words if w and w.casefold() not in _MINOR_WORDS]
    if not significant or not all(w[0].isupper() for w in significant):
        return False
    following = lines[index + 1 :]
    return bool(following) and not following[0].strip() and any(x.strip() for x in following)
