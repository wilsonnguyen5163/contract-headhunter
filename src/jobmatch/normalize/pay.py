"""Pay from posting text, English and French.

Reads "$70,000 - $85,000 per year", "70 000 $ à 85 000 $ par année", "$45/hr",
"90 $ de l'heure", "$100K-$120K", and bare amounts under a pay header
("Hiring Min Rate / 70000"). Outside a pay section an amount needs a currency sign and a
pay or unit word on the same line, so "$250,000 budget" is not read as pay.
The unit comes from the wording unless it is implausible for the amount (pay.units in
normalization.yaml); currency is USD with a US marker, otherwise CAD when a "$" or CAD
marker is present (Canada-only scope), otherwise unknown.
"""

import re

from pydantic import BaseModel

from jobmatch.config import PayRules
from jobmatch.normalize.phrases import fold
from jobmatch.schemas import PayUnit

_AMOUNT = re.compile(
    r"(?P<pre>(?:us|ca|c)?\$\s?|\b(?:usd|cad)\s?)?"
    r"(?<![\w.,])(?P<num>\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"(?:\s?(?P<k>k)(?![a-z]))?"
    r"(?P<scale>\s?(?:m|mm|million|millions|b|billion|milliards?)\b)?"
    r"(?P<post>\s?(?:\$|cad\b|usd\b))?"
)
_THOUSANDS = re.compile(r"(?<=\d)[ ,](?=\d{3}(?!\d))")
# Text between the two ends of a range: "60-75", "70 000 $ à 85 000 $", "between 60 and 75".
_RANGE_GAP = re.compile(r"^\s*(?:-|–|—|to|a|and|et|ou|or)\s*$")


class PayInfo(BaseModel):
    """pay_max is None for a single amount ("$75,000"), which is not a range."""

    pay_min: float
    pay_max: float | None
    unit: PayUnit | None
    currency: str | None

    @classmethod
    def of(cls, values: list[float], unit: PayUnit | None, currency: str | None) -> "PayInfo":
        low, high = min(values), max(values)
        return cls(pay_min=low, pay_max=high if high != low else None, unit=unit, currency=currency)


class _Amount(BaseModel):
    value: float
    has_currency: bool
    start: int
    end: int


def _term_regex(terms: list[str]) -> re.Pattern[str] | None:
    folded = sorted({fold(t).strip() for t in terms if t.strip()}, key=len, reverse=True)
    if not folded:
        return None
    return re.compile("|".join(rf"(?<![a-z]){re.escape(t)}(?![a-z])" for t in folded))


class PayParser:
    def __init__(self, rules: PayRules) -> None:
        self._units = rules.units
        self._unit_terms = {
            PayUnit(name): _term_regex(rule.terms) for name, rule in rules.units.items()
        }
        self._context = _term_regex(rules.context_terms)
        self._usd = _term_regex(rules.usd_markers)
        self._cad = _term_regex(rules.cad_markers)

    def parse(self, pay_text: str, body_text: str) -> PayInfo | None:
        """Pay from the pay sections first, else from a pay line elsewhere in the job text."""
        return self._from_pay_section(pay_text) or self._from_body(body_text)

    def _from_pay_section(self, text: str) -> PayInfo | None:
        folded = fold(text)
        amounts = [
            a
            for line in folded.split("\n")
            for a in self._amounts(line)
            if a.has_currency or a.value >= 1000 or self._first_unit(line) is not None
        ]
        return self._build(amounts, folded)

    def _from_body(self, text: str) -> PayInfo | None:
        for line in fold(text).split("\n"):
            amounts = [a for a in self._amounts(line) if a.has_currency]
            has_context = self._context is not None and self._context.search(line)
            if amounts and (has_context or self._first_unit(line) is not None):
                return self._build(amounts, line)
        return None

    def _amounts(self, line: str) -> list[_Amount]:
        found: list[_Amount] = []
        for m in _AMOUNT.finditer(line):
            if m.group("scale"):
                continue  # "$5M budget"
            value = float(_THOUSANDS.sub("", m.group("num")).replace(",", "."))
            if m.group("k"):
                value *= 1000
            has_currency = bool(m.group("pre") or m.group("post"))
            if (
                not has_currency
                and not m.group("k")
                and 1900 <= value <= 2100
                and value.is_integer()
            ):
                continue  # a year, not an amount
            found.append(
                _Amount(value=value, has_currency=has_currency, start=m.start(), end=m.end())
            )
        # The other end of a range shares the currency: "USD 60-75", "$100K-120K".
        for left, right in zip(found, found[1:], strict=False):
            if left.has_currency != right.has_currency and _RANGE_GAP.match(
                line[left.end : right.start]
            ):
                left.has_currency = right.has_currency = True
        return found

    def _first_unit(self, text: str) -> PayUnit | None:
        best: tuple[int, PayUnit] | None = None
        for unit, regex in self._unit_terms.items():
            match = regex.search(text) if regex else None
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), unit)
        return best[1] if best else None

    def _plausible(self, unit: PayUnit, amount: float) -> bool:
        rule = self._units[unit.value]
        return rule.min <= amount <= rule.max

    def _build(self, amounts: list[_Amount], context: str) -> PayInfo | None:
        if not amounts:
            return None
        values = [a.value for a in amounts[:2]]
        high = max(values)
        if high < self._units["hour"].min:
            return None
        unit = self._first_unit(context)
        if unit is None or not self._plausible(unit, high):
            if high >= self._units["year"].min:
                unit = PayUnit.YEAR
            elif high <= self._units["hour"].max:
                unit = PayUnit.HOUR
            else:
                unit = None
        if self._usd is not None and self._usd.search(context):
            currency: str | None = "USD"
        elif any(a.has_currency for a in amounts) or (self._cad and self._cad.search(context)):
            currency = "CAD"
        else:
            currency = None
        return PayInfo.of(values, unit, currency)
