"""Company normalization: casefold, strip legal suffixes (Inc., Ltée...), apply aliases."""

import re

from jobmatch.config import CompanyAliases
from jobmatch.normalize.phrases import fold
from jobmatch.normalize.text import clean_text

_TOKEN_EDGE = ".,;:()"
_SPACES = re.compile(r"\s+")


class CompanyNormalizer:
    def __init__(self, aliases: CompanyAliases) -> None:
        self._suffixes = {fold(s).strip(_TOKEN_EDGE) for s in aliases.legal_suffixes}
        self._aliases: dict[str, str] = {}
        for canonical, variants in aliases.aliases.items():
            for name in (canonical, *variants):
                self._aliases[fold(name).strip()] = canonical.casefold()

    def normalize(self, company: str | None) -> str | None:
        """e.g. "Example Financial Inc." -> "example financial"; "RBC" -> "royal bank of canada"."""
        if not company:
            return None
        tokens = _SPACES.sub(" ", clean_text(company).casefold()).split(" ")
        tokens = [t for t in tokens if t.strip(_TOKEN_EDGE)]
        while len(tokens) > 1 and fold(tokens[-1]).strip(_TOKEN_EDGE) in self._suffixes:
            tokens.pop()
        name = " ".join(tokens).strip(_TOKEN_EDGE + "& ")
        return self._aliases.get(fold(name), name) or None
