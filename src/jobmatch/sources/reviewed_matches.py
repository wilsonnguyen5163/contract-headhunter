"""Read a reviewed-match sheet export (CSV) into ReviewedMatch rows: the human labels the
matcher is evaluated against. Only contract-type engagements are kept; permanent roles are
out of scope. Contact, outreach and talent-name columns are never read.

Column names and rules live in config/reviewed_import.yaml. The sheet holds personal data:
this module never logs names or free text.
"""

import csv
from pathlib import Path

import yaml
from pydantic import BaseModel

from jobmatch.config import StrictModel
from jobmatch.schemas import ReviewedMatch

REVIEWED_IMPORT_FILE = "reviewed_import.yaml"


class ReviewedColumns(StrictModel):
    match_id: str
    talent_id: str
    company: str
    role: str
    work_setting: str
    engagement: str
    posting_url: str
    posting_status: str
    match_score: str
    match_tier: str
    rationale: str
    gaps: str
    notes: str
    skills: str
    availability: str
    location: str
    contract_likelihood: str
    posting_age: str
    buyer_urgency: str


class ReviewedImportRules(StrictModel):
    version: int
    columns: ReviewedColumns
    out_of_scope_prefixes: list[str]
    tiers: dict[str, list[str]]


class ReviewedImportReport(BaseModel):
    rows: int  # filled-in rows in the sheet
    kept: int
    permanent: int  # out of scope
    no_engagement: int


class SheetError(ValueError):
    pass


def load_reviewed_import(config_dir: Path) -> ReviewedImportRules:
    with (config_dir / REVIEWED_IMPORT_FILE).open(encoding="utf-8") as f:
        return ReviewedImportRules.model_validate(yaml.safe_load(f))


def _number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # a note typed into a score cell is treated as no score


def _tier(value: str, tiers: dict[str, list[str]]) -> str | None:
    key = " ".join(value.casefold().split())
    if not key:
        return None
    for label, spellings in tiers.items():
        if key in spellings:
            return label
    return key  # an unknown tier is kept as written, lowercased


def read_reviewed_sheet(
    path: Path, rules: ReviewedImportRules
) -> tuple[list[ReviewedMatch], ReviewedImportReport]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        cols = rules.columns.model_dump()
        missing = [name for name in cols.values() if name not in header]
        if missing:
            raise SheetError(f"{path.name}: missing column(s): {', '.join(missing)}")
        raw_rows = [r for r in reader if (r.get(cols["match_id"]) or "").strip()]

    prefixes = tuple(p.casefold() for p in rules.out_of_scope_prefixes)
    kept: list[ReviewedMatch] = []
    permanent = no_engagement = 0
    for row in raw_rows:
        cell = {field: (row.get(column) or "").strip() for field, column in cols.items()}
        engagement = cell["engagement"]
        if not engagement:
            no_engagement += 1
            continue
        if engagement.casefold().startswith(prefixes):
            permanent += 1
            continue
        kept.append(
            ReviewedMatch(
                match_id=cell["match_id"],
                talent_id=cell["talent_id"],
                company=cell["company"] or None,
                role=cell["role"] or None,
                work_setting=cell["work_setting"] or None,
                engagement=engagement,
                posting_url=cell["posting_url"] or None,
                posting_status=cell["posting_status"] or None,
                match_score=_number(cell["match_score"]),
                match_tier=_tier(cell["match_tier"], rules.tiers),
                skills=_number(cell["skills"]),
                availability=_number(cell["availability"]),
                location=_number(cell["location"]),
                contract_likelihood=_number(cell["contract_likelihood"]),
                posting_age=_number(cell["posting_age"]),
                buyer_urgency=_number(cell["buyer_urgency"]),
                rationale=cell["rationale"] or None,
                gaps=cell["gaps"] or None,
                notes=cell["notes"] or None,
            )
        )
    report = ReviewedImportReport(
        rows=len(raw_rows), kept=len(kept), permanent=permanent, no_engagement=no_engagement
    )
    return kept, report
