"""Compose the clean job description from the parsed parts, always in the same order:

    <position name, from the title>

    Responsibilities
    - ...

    Requirements
    - ...
    Assets
    - ...

    Pay
    Annual salary: 70,000–85,000 CAD

Intro and overview prose, reporting lines, metadata, application steps, pitch and benefits
are left out. Only when a posting has neither responsibilities nor requirements (no
headers at all) is its pruned prose kept, under "Description", so there is something to
match on. Section labels are English for every posting so the text has one shape (the
embedding model is multilingual).
"""

from jobmatch.normalize.pay import PayInfo
from jobmatch.schemas import PayUnit

_PAY_LABELS = {
    PayUnit.HOUR: "Hourly rate",
    PayUnit.DAY: "Daily rate",
    PayUnit.WEEK: "Weekly pay",
    PayUnit.MONTH: "Monthly pay",
    PayUnit.YEAR: "Annual salary",
}


def format_amount(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"


def format_pay(pay: PayInfo) -> str:
    amount = format_amount(pay.pay_min)
    if pay.pay_max is not None and pay.pay_max != pay.pay_min:
        amount = f"{amount}–{format_amount(pay.pay_max)}"
    if pay.currency:
        amount = f"{amount} {pay.currency}"
    label = _PAY_LABELS.get(pay.unit, "Pay") if pay.unit else "Pay"
    return f"{label}: {amount}"


def compose_description(
    title: str,
    responsibilities: list[str],
    requirements: list[str],
    nice_to_have: list[str],
    pay: PayInfo | None,
    *,
    fallback: list[str],
) -> str:
    parts = [title]
    if responsibilities:
        parts.append("Responsibilities\n" + "\n".join(f"- {r}" for r in responsibilities))
    if requirements or nice_to_have:
        block: list[str] = []
        if requirements:
            block += ["Requirements", *(f"- {r}" for r in requirements)]
        if nice_to_have:
            block += ["Assets", *(f"- {n}" for n in nice_to_have)]
        parts.append("\n".join(block))
    if not responsibilities and not requirements and not nice_to_have and fallback:
        parts.append("Description\n" + "\n".join(fallback))
    if pay:
        parts.append(f"Pay\n{format_pay(pay)}")
    return "\n\n".join(parts)
