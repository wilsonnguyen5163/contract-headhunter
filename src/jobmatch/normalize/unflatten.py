"""Restore structure to posting text that arrives flattened into one long line (Workday's
structured data, some saved pages): "...brand opportunities Who You Are You are passionate
about... What You'll Do Create engaging content... next Defining and tracking...".

Three steps, before sections are found:
1. Known section headers (normalization.yaml) go on their own line. Inside running text a
   header counts only when followed by ":" or written in Title Case and followed by a
   capitalized word, so "five years of experience" stays a phrase.
2. Inside responsibilities, requirements and nice-to-have, the text is split into one item
   per line: at sentence ends, and before a capitalized item starter ("Create", "Defining",
   "Experience") that follows a lowercase word.
3. Pay sentences ("The expected salary range for this role is $110,300 to $149,300 CAD.")
   move to a Pay section at the end, so amounts in a benefits paragraph ("$2000 annual
   counseling benefit") are not read as pay.

Only applied to long text whose lines average at least min_chars_per_line characters
(flattened_text in normalization.yaml); other postings pass through unchanged.
"""

import re

from jobmatch.config import NormalizationRules
from jobmatch.normalize.phrases import fold

_LIST_KINDS = {"responsibilities", "requirements", "nice_to_have"}
_MINOR = {
    "and", "&", "of", "the", "for", "to", "in", "on", "with", "a", "an", "or", "you",
    "et", "de", "des", "du", "la", "le", "les", "en", "pour", "au", "aux", "à", "ou",
}  # fmt: skip
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[\"'(]?[A-ZÀ-Ý$\d])")
_LEAD_IN = re.compile(
    r"^(.{0,160}?\b(?:you will|you'll|vous allez|vous aurez à|vous serez appelée? à)\s*:)\s*",
    re.IGNORECASE,
)
_CLAUSE_START = re.compile(r"(?:^|\s)([A-ZÀ-Ý][\w'’]*)")
_AMOUNT = re.compile(r"(?:\$|\bcad\b|\busd\b)\s?\d|\d[\d ,.]*\s?(?:\$|k\b)", re.IGNORECASE)


class Unflattener:
    def __init__(self, rules: NormalizationRules) -> None:
        self._rules = rules.flattened_text
        self._kinds: dict[str, str] = {}
        for kind, rule in rules.sections.items():
            for header in rule.headers.all():
                self._kinds.setdefault(fold(header.translate(_APOSTROPHES)).strip(), kind)
        phrases = sorted(self._kinds, key=len, reverse=True)
        body = "|".join(r"\s+".join(re.escape(w) for w in p.split()) for p in phrases)
        self._header = re.compile(rf"(?<![\w'])({body})(?![\w'])", re.IGNORECASE)
        terms = [fold(t) for t in rules.pay.context_terms]
        self._pay_word = re.compile(
            "|".join(rf"(?<![a-z]){re.escape(t)}(?![a-z])" for t in terms) or r"(?!x)x"
        )
        if self._rules is not None:
            starters = [w.casefold() for w in self._rules.item_starters.all()]
            self._starters = {fold(w) for w in starters}
            self._ing_nouns = {w.casefold() for w in self._rules.ing_nouns}

    def is_flattened(self, text: str) -> bool:
        r = self._rules
        if r is None or len(text) < r.min_chars:
            return False
        lines = [line for line in text.split("\n") if line.strip()]
        return len(text) / max(1, len(lines)) >= r.min_chars_per_line

    def unflatten(self, text: str) -> str:
        if not self.is_flattened(text):
            return text
        text = " ".join(text.translate(_APOSTROPHES).split())
        pieces: list[tuple[str | None, str]] = []  # (header kind, text)
        position, kind = 0, None
        for match in self._header.finditer(text):
            if not self._is_header(text, match):
                continue
            pieces.append((kind, text[position : match.start()]))
            pieces.append((None, f"\n\n{match.group(1)}\n"))
            kind = self._kinds[fold(" ".join(match.group(1).split()))]
            end = match.end()
            while end < len(text) and text[end] in " :":
                end += 1
            position = end
        pieces.append((kind, text[position:]))

        out: list[str] = []
        pay: list[str] = []
        for piece_kind, segment in pieces:
            if piece_kind is None and segment.startswith("\n"):
                out.append(segment)
                continue
            sentences = _SENTENCE_END.split(segment.strip())
            kept = []
            for sentence in sentences:
                rest, clause = self._pay_clause(sentence)
                if clause:
                    pay.append(clause)
                if rest:
                    kept.append(rest)
            if piece_kind in _LIST_KINDS:
                out.append(self._items(" ".join(kept)))
            else:
                out.append(" ".join(kept))
        if pay:
            out.append("\n\nPay\n" + "\n".join(pay))
        return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()

    def _is_header(self, text: str, match: re.Match[str]) -> bool:
        written = match.group(1)
        after = text[match.end() :]
        if after.lstrip().startswith(":"):
            return True
        if not re.match(r"\s+[A-ZÀ-Ý]", after):
            return False
        words = written.split()
        if len(words) < 2:
            return False
        significant = [w for w in words if w.casefold() not in _MINOR]
        return bool(significant) and all(w[0].isupper() for w in significant)

    def _pay_clause(self, sentence: str) -> tuple[str, str]:
        """Split a sentence into (rest, pay clause). The pay clause is the shortest tail,
        starting at a capitalized word, that holds both a pay word and an amount: run-on
        benefit lists ("...$2000 annual counseling benefit ... 10 years The expected salary
        range is $110,300 to $149,300 CAD.") keep their amounts out of pay."""
        if not (_AMOUNT.search(sentence) and self._pay_word.search(fold(sentence))):
            return sentence, ""
        for match in reversed(list(_CLAUSE_START.finditer(sentence))):
            tail = sentence[match.start(1) :]
            if _AMOUNT.search(tail) and self._pay_word.search(fold(tail)):
                return sentence[: match.start(1)].strip(), tail.strip()
        return "", sentence

    def _items(self, segment: str) -> str:
        """One item per line ("- item"); a lead-in like "As a ..., you will:" stays a line
        ending in ":" so it is not taken as an item."""
        lines: list[str] = []
        lead = _LEAD_IN.match(segment)
        if lead:
            lines.append(lead.group(1))
            segment = segment[lead.end() :]
        for sentence in _SENTENCE_END.split(segment):
            for item in self._split_starters(sentence):
                item = item.strip(" ;")
                if len(item) > 2:
                    lines.append(f"- {item}")
        return "\n" + "\n".join(lines) + "\n"

    def _split_starters(self, sentence: str) -> list[str]:
        words = sentence.split(" ")
        items: list[list[str]] = [[]]
        for i, word in enumerate(words):
            previous = words[i - 1] if i else ""
            if (
                i
                and items[-1]
                and previous[-1:].islower()
                and previous.casefold() not in _MINOR
                and self._starts_item(word)
            ):
                items.append([])
            items[-1].append(word)
        return [" ".join(w) for w in items if w]

    def _starts_item(self, word: str) -> bool:
        clean = word.strip(",;:.()\"'")
        if not clean or not clean[0].isupper() or not clean[1:].islower():
            return False
        key = clean.casefold()
        if fold(key) in self._starters:
            return True
        return key.endswith("ing") and len(key) > 5 and key not in self._ing_nouns
