"""Split posting text into sections by header lines; drop boilerplate; pull requirement bullets.

A header is a line that matches a known header (normalization.yaml), optionally followed by
":" and inline text ("About us: we are..."). Unknown sub-headers ("Technical skills:") are
ordinary lines, so they stay inside the section they appear in.
"""

import re
from enum import StrEnum

from jobmatch.config import NormalizationRules
from jobmatch.normalize.phrases import fold

_HEADER_DECORATION = " \t#*•·▪-–—=_"
_BULLET = re.compile(r"^(?:[•·▪◦●\-*–—✓✔→>]+|\(?\d{1,2}[.)])\s*")
_MAX_REQUIREMENT_CHARS = 300


class SectionKind(StrEnum):
    INTRO = "intro"  # text before the first header
    BOILERPLATE = "boilerplate"
    REQUIREMENTS = "requirements"
    OTHER = "other"


class SectionSplitter:
    def __init__(self, rules: NormalizationRules) -> None:
        self._headers: dict[str, SectionKind] = {}
        for kind, headers in (
            (SectionKind.OTHER, rules.section_headers.all()),
            (SectionKind.REQUIREMENTS, rules.requirement_headers.all()),
            (SectionKind.BOILERPLATE, rules.boilerplate_headers.all()),
        ):
            for header in headers:
                self._headers[_header_key(header)] = kind

    def classify(self, line: str) -> tuple[SectionKind, str] | None:
        """Return (kind, inline text after the header) if the line is a known header."""
        stripped = line.strip(_HEADER_DECORATION)
        if not stripped:
            return None
        kind = self._headers.get(_header_key(stripped))
        if kind is not None:
            return kind, ""
        if ":" in stripped:
            head, rest = stripped.split(":", 1)
            kind = self._headers.get(_header_key(head))
            if kind is not None:
                return kind, rest.strip()
        return None

    def split(self, text: str) -> list[tuple[SectionKind, list[str]]]:
        """Sections in order as (kind, lines); a section's first line is its header."""
        sections: list[tuple[SectionKind, list[str]]] = [(SectionKind.INTRO, [])]
        for line in text.split("\n"):
            header = self.classify(line)
            if header is None:
                sections[-1][1].append(line)
            else:
                sections.append((header[0], [line]))
        return [(kind, lines) for kind, lines in sections if lines]

    def strip_boilerplate(self, text: str) -> str:
        kept = [
            "\n".join(lines)
            for kind, lines in self.split(text)
            if kind is not SectionKind.BOILERPLATE
        ]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    def requirements(self, text: str, limit: int) -> list[str]:
        items: list[str] = []
        for kind, lines in self.split(text):
            if kind is not SectionKind.REQUIREMENTS:
                continue
            header = self.classify(lines[0])
            inline = header[1] if header else ""
            for line in [inline, *lines[1:]]:
                item = _BULLET.sub("", line.strip()).strip()
                if len(item) >= 3 and not item.endswith(":"):
                    items.append(item[:_MAX_REQUIREMENT_CHARS])
        return list(dict.fromkeys(items))[:limit]


def _header_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text).strip(_HEADER_DECORATION + ":")).strip()
