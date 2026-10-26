"""Build the contractor list from the contractors master workbook (two tabs joined on
Talent ID). Only people whose Contract? flag is true are exported. Rules and column names
live in config/contractor_import.yaml.

Output columns, one row per contractor:
  talent_id, talent_name, location, standardized_headline, executive_summary,
  core_strengths, proof_points, main_industry, seniority, weekly_hours,
  rate_expectation (per hour), target_titles, contract

Contractor data is personal information: this module never logs names or profile text.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from jobmatch.config import StrictModel
from jobmatch.schemas import ContractorProfile

CONTRACTOR_IMPORT_FILE = "contractor_import.yaml"

_RATE_NUMBER = re.compile(r"\$\s*(\d{1,4}(?:[.,]\d{1,2})?)|(\d{1,4}(?:[.,]\d{1,2})?)\s*\$")
_NOT_HOURLY = re.compile(r"\b(?:year|yr|annual|annually|salary|per annum|/an|par an|k)\b")
_SPACES = re.compile(r"\s+")


# ---- config ----


class TalentColumns(StrictModel):
    name: str
    city: str
    province: str
    primary_role: str
    weekly_hours: str


class ProfileColumns(StrictModel):
    location: str
    headline: str
    summary: str
    core_strengths: str
    proof_points: str
    industries: str
    seniority: str
    rate: str
    target_titles: list[str]


class ImportColumns(StrictModel):
    talent: TalentColumns
    profile: ProfileColumns


class ContractorImportRules(StrictModel):
    version: int
    talent_sheet: str
    profile_sheet: str
    id_column: str
    contract_column: str
    contract_true_values: list[str]
    location_placeholders: list[str]
    rate_default: str = Field(min_length=1)
    list_separators: list[str]
    columns: ImportColumns


# ---- output ----


# The exported row is the shared ContractorProfile model (schemas.py).
ContractorRow = ContractorProfile


class BuildReport(BaseModel):
    """Counts only (no names), safe to print or log."""

    exported: int
    with_profile: int
    without_profile: int
    not_contract: int  # talent rows whose Contract? flag is not true
    profile_without_talent: int  # profiles with no tab-1 row, so no Contract? flag
    rates_numeric: int
    rates_default: int


# ---- rules ----


def parse_rate(text: str | None) -> float | None:
    """Hourly rate from free text: "$150/hr" -> 150, "$45-$60/hr" -> 52.5 (the average),
    "$80+/hr" -> 80. None when there is no "$" amount, or when it is not hourly ("$90,000/year")."""
    if not text:
        return None
    folded = text.casefold()
    if _NOT_HOURLY.search(folded):
        return None
    numbers = [float((a or b).replace(",", ".")) for a, b in _RATE_NUMBER.findall(folded)][:2]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def split_list(text: str | None, separators: list[str]) -> list[str]:
    if not text:
        return []
    pattern = "|".join(re.escape(s) for s in separators)
    items = [_SPACES.sub(" ", part).strip(" .") for part in re.split(pattern, text)]
    return list(dict.fromkeys(i for i in items if i))


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = _SPACES.sub(" ", str(value)).strip()
    return text or None


def _hours(value: str | None) -> float | None:
    try:
        number = float(value) if value else None
    except ValueError:
        return None  # "#N/A", text
    return number if number is not None and number >= 0 else None


def _unique_titles(titles: list[str | None]) -> list[str]:
    seen: dict[str, str] = {}
    for title in titles:
        cleaned = _clean(title)
        if cleaned:
            seen.setdefault(cleaned.casefold(), cleaned)
    return list(seen.values())


def build_contractors(
    sheets: Mapping[str, list[dict[str, str]]], rules: ContractorImportRules
) -> tuple[list[ContractorRow], BuildReport]:
    if rules.talent_sheet not in sheets or rules.profile_sheet not in sheets:
        missing = [s for s in (rules.talent_sheet, rules.profile_sheet) if s not in sheets]
        raise ValueError(f"workbook has no tab named {', '.join(repr(m) for m in missing)}")
    t_cols, p_cols = rules.columns.talent, rules.columns.profile
    true_values = {v.casefold() for v in rules.contract_true_values}
    placeholders = {p.casefold() for p in rules.location_placeholders}

    talent_rows = [r for r in sheets[rules.talent_sheet] if _clean(r.get(rules.id_column))]
    profiles = {
        _clean(r.get(rules.id_column)): r
        for r in sheets[rules.profile_sheet]
        if _clean(r.get(rules.id_column))
    }
    talent_ids = {_clean(r.get(rules.id_column)) for r in talent_rows}

    exported: list[ContractorRow] = []
    not_contract = with_profile = rates_numeric = 0
    for talent in talent_rows:
        talent_id = _clean(talent.get(rules.id_column))
        if (_clean(talent.get(rules.contract_column)) or "").casefold() not in true_values:
            not_contract += 1
            continue
        profile = profiles.get(talent_id, {})
        with_profile += bool(profile)

        location = _clean(profile.get(p_cols.location))
        if location is None or location.casefold() in placeholders:
            parts = [_clean(talent.get(t_cols.city)), _clean(talent.get(t_cols.province))]
            location = ", ".join(p for p in parts if p) or location

        primary_role = _clean(talent.get(t_cols.primary_role))
        rate = parse_rate(profile.get(p_cols.rate))
        rates_numeric += rate is not None

        exported.append(
            ContractorRow(
                talent_id=talent_id or "",
                talent_name=_clean(talent.get(t_cols.name))
                or _clean(profile.get(t_cols.name))
                or talent_id
                or "",
                location=location,
                standardized_headline=_clean(profile.get(p_cols.headline)) or primary_role,
                executive_summary=_clean(profile.get(p_cols.summary)),
                core_strengths=split_list(
                    profile.get(p_cols.core_strengths), rules.list_separators
                ),
                proof_points=split_list(profile.get(p_cols.proof_points), rules.list_separators),
                main_industry=split_list(profile.get(p_cols.industries), rules.list_separators),
                seniority=_clean(profile.get(p_cols.seniority)),
                weekly_hours=_hours(_clean(talent.get(t_cols.weekly_hours))),
                rate_expectation=format_number(rate) if rate is not None else rules.rate_default,
                target_titles=_unique_titles(
                    [primary_role, *(profile.get(c) for c in p_cols.target_titles)]
                ),
            )
        )

    report = BuildReport(
        exported=len(exported),
        with_profile=with_profile,
        without_profile=len(exported) - with_profile,
        not_contract=not_contract,
        profile_without_talent=len(set(profiles) - talent_ids),
        rates_numeric=rates_numeric,
        rates_default=len(exported) - rates_numeric,
    )
    return exported, report


def default_output(workbook: Path) -> Path:
    return workbook.with_name("contractors.csv")


def load_contractor_import(config_dir: Path) -> ContractorImportRules:
    with (config_dir / CONTRACTOR_IMPORT_FILE).open(encoding="utf-8") as f:
        return ContractorImportRules.model_validate(yaml.safe_load(f))
