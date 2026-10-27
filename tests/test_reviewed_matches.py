"""Reviewed-match sheet import (sources/reviewed_matches.py). Rows are synthetic."""

import csv
from pathlib import Path

import pytest

from conftest import CONFIG_DIR
from jobmatch.sources.reviewed_matches import SheetError, load_reviewed_import, read_reviewed_sheet

RULES = load_reviewed_import(CONFIG_DIR)
COLUMNS = list(RULES.columns.model_dump().values())
EXTRA = ["Talent Name", "Contact 1 Name", "Contact 1 LinkedIn / Source", "Owner"]


def write_sheet(path: Path, rows: list[dict[str, str]], header: list[str] | None = None) -> Path:
    header = header or [*COLUMNS[:3], *EXTRA, *COLUMNS[3:]]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})
    return path


def row(match_id: str, engagement: str, **cells: str) -> dict[str, str]:
    base = {
        "Match ID": match_id,
        "Talent ID": "TAL-X-001",
        "Talent Name": "Sample Person",
        "Contact 1 Name": "Sample Contact",
        "Contact 1 LinkedIn / Source": "https://example.org/profile",
        "Company": "Example Co",
        "Role": "Content Producer - 12-Month Fixed-Term Contract",
        "Engagement Type": engagement,
        "Posting URL": "https://example.org/jobs/1",
        "Posting Status": "Active",
        "Match Score": "89",
        "Match Tier": "A - Strong",
        "Skills Match (30)": "28",
        "Availability Match (15)": "13",
        "Location Match (15)": "15",
        "Contract Likelihood (20)": "20",
        "Posting Age (10)": "6",
        "Buyer Urgency (10)": "7",
        "Match Rationale": "SYNTHETIC rationale.",
    }
    base.update(cells)
    return base


def test_contract_rows_are_kept_and_permanent_rows_left_out(tmp_path: Path) -> None:
    sheet = write_sheet(
        tmp_path / "raw.csv",
        [
            row("M-1", "Fixed-term - 12 months"),
            row("M-2", "Permanent"),
            row("M-3", "Permanent - contract-conversion target"),
            row("M-4", "Fractional contract", **{"Match Tier": "B - Good", "Match Score": "77"}),
            row("M-5", "", **{"Match Tier": "Reject"}),
            {"Match ID": ""},  # blank sheet row
        ],
    )
    kept, report = read_reviewed_sheet(sheet, RULES)
    assert [m.match_id for m in kept] == ["M-1", "M-4"]
    assert (report.rows, report.kept, report.permanent, report.no_engagement) == (5, 2, 2, 1)
    first = kept[0]
    assert (first.match_score, first.match_tier, first.skills, first.buyer_urgency) == (
        89.0,
        "A",
        28.0,
        7.0,
    )
    assert kept[1].match_tier == "B"


def test_names_and_contacts_are_never_read(tmp_path: Path) -> None:
    kept, _ = read_reviewed_sheet(
        write_sheet(tmp_path / "raw.csv", [row("M-1", "Contract")]), RULES
    )
    dumped = kept[0].model_dump_json()
    assert "Sample Person" not in dumped and "Sample Contact" not in dumped
    assert "example.org/profile" not in dumped


def test_tiers_and_empty_scores(tmp_path: Path) -> None:
    rows = [
        row("M-1", "Contract", **{"Match Tier": "Reject", "Match Score": ""}),
        row("M-2", "Contract", **{"Match Tier": "C - Stretch", "Skills Match (30)": "n/a"}),
    ]
    kept, _ = read_reviewed_sheet(write_sheet(tmp_path / "raw.csv", rows), RULES)
    assert (kept[0].match_tier, kept[0].match_score) == ("reject", None)
    assert (kept[1].match_tier, kept[1].skills) == ("C", None)


def test_missing_column_is_named(tmp_path: Path) -> None:
    header = [c for c in COLUMNS if c != "Match Tier"]
    sheet = write_sheet(tmp_path / "raw.csv", [row("M-1", "Contract")], header=header)
    with pytest.raises(SheetError, match="missing column\\(s\\): Match Tier"):
        read_reviewed_sheet(sheet, RULES)
