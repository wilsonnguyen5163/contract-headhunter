"""build-contractors on a small synthetic workbook (invented people only)."""

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from typer.testing import CliRunner

from conftest import CONFIG_DIR
from jobmatch.cli import app
from jobmatch.sources.contractor_sheet import (
    build_contractors,
    load_contractor_import,
    parse_rate,
    split_list,
)
from jobmatch.sources.xlsx import read_sheets

RULES = load_contractor_import(CONFIG_DIR)

TALENT = [
    [
        "Talent ID",
        "Talent Name",
        "City",
        "Province",
        "Primary Role",
        "Contract?",
        "Weekly Capacity",
    ],
    ["TAL-1", "Sample Person A", "Calgary", "AB", "Sr. Digital Project Manager", "Yes", "40.0"],
    ["TAL-2", "Sample Person B", "Vancouver", "BC", "SEO Strategist", "Yes", "20.0"],
    ["TAL-3", "Sample Person C", "Toronto", "ON", "Designer", "No", "40.0"],
    ["TAL-4", "Sample Person D", "Montréal", "QC", "Analyste", "", "10.0"],
    ["TAL-5", "Sample Person E", "Ottawa", "ON", "Writer", "yes", "#N/A"],
]
PROFILES = [
    [
        "Talent ID",
        "Talent Name",
        "Location",
        "Standardized Headline",
        "Executive Summary",
        "Core Strengths",
        "Proof Points",
        "Industry Fit",
        "Seniority",
        "Rate Expectation",
        "Target Title 1",
        "Target Title 2",
        "Target Title 3",
        "Target Title 4",
        "Target Title 5",
    ],
    [
        "TAL-1",
        "Sample Person A",
        "Location to confirm",
        "Senior Digital & Technical Project Manager",
        "SYNTHETIC. Leads digital programs.",
        "Program delivery; website migrations; agile/waterfall",
        "Managed 40+ staff; $10M budgets",
        "Agencies; technology; retail",
        "Senior / Lead",
        "$45-$60/hr (confirm)",
        "Digital Project Manager",
        "sr. digital project manager",
        "Program Manager",
        "",
        "",
    ],
    [
        "TAL-2",
        "Sample Person B",
        "Vancouver, BC",
        "SEO & Content Strategy Consultant",
        "SYNTHETIC.",
        "SEO; content",
        "",
        "SaaS",
        "Mid-level",
        "Needs Confirming",
        "SEO Strategist",
        "Content Strategist",
        "",
        "",
        "",
    ],
    ["TAL-9", "Sample Person Z", "Halifax, NS", "Profile only", *[""] * 11],
]


def workbook(path: Path, tabs: dict[str, list[list[str]]]) -> Path:
    """A minimal .xlsx (inline strings) with the given tabs."""

    def column(i: int) -> str:
        letters = ""
        i += 1
        while i:
            i, rem = divmod(i - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    with zipfile.ZipFile(path, "w") as z:
        sheets_xml, rels_xml = [], []
        for n, (name, rows) in enumerate(tabs.items(), start=1):
            sheets_xml.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
            rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            rels_xml.append(
                f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml" '
                f'Type="{rel_type}/worksheet"/>'
            )
            body = "".join(
                f'<row r="{r + 1}">'
                + "".join(
                    f'<c r="{column(c)}{r + 1}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
                    for c, v in enumerate(row)
                    if v
                )
                + "</row>"
                for r, row in enumerate(rows)
            )
            z.writestr(
                f"xl/worksheets/sheet{n}.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{body}</sheetData></worksheet>",
            )
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(sheets_xml)}</sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(rels_xml)}</Relationships>",
        )
    return path


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return workbook(tmp_path / "master.xlsx", {"Talent": TALENT, "Talent Profiles": PROFILES})


def test_only_confirmed_contractors_are_exported(book: Path) -> None:
    rows, report = build_contractors(read_sheets(book), RULES)
    assert [r.talent_id for r in rows] == ["TAL-1", "TAL-2", "TAL-5"]  # "No" and blank excluded
    assert (report.not_contract, report.profile_without_talent) == (2, 1)
    assert (report.with_profile, report.without_profile) == (2, 1)
    assert all(r.contract for r in rows)


def test_fields_are_joined_and_cleaned(book: Path) -> None:
    rows, _ = build_contractors(read_sheets(book), RULES)
    first = rows[0]
    assert first.location == "Calgary, AB"  # placeholder in the profile -> tab-1 city
    assert first.standardized_headline == "Senior Digital & Technical Project Manager"
    assert first.core_strengths == ["Program delivery", "website migrations", "agile/waterfall"]
    assert first.main_industry == ["Agencies", "technology", "retail"]  # whole list, in order
    assert first.target_titles == [
        "Sr. Digital Project Manager",  # Primary Role first; its repeat in Target Title 2 dropped
        "Digital Project Manager",
        "Program Manager",
    ]
    assert first.weekly_hours == 40
    assert first.rate_expectation == "52.5"  # average of the range


def test_contractor_without_profile_keeps_tab_one_fields(book: Path) -> None:
    rows, _ = build_contractors(read_sheets(book), RULES)
    thin = rows[2]
    assert thin.standardized_headline == "Writer"  # Primary Role stands in for the headline
    assert thin.location == "Ottawa, ON"
    assert thin.weekly_hours is None  # "#N/A"
    assert thin.rate_expectation == "Industry Standard"
    assert thin.executive_summary is None and thin.core_strengths == []


@pytest.mark.parametrize(
    ("text", "rate"),
    [
        ("$150/hr (confirm)", 150),
        ("$150+/hr", 150),
        ("$45-$60/hr (confirm)", 52.5),
        ("$60–$80/hr", 70),
        ("$125–$175/hr", 150),
        ("$80/hr proposed for Flywheel; standard rate remains scope-dependent", 80),
        ("85 $/h", 85),
        ("Needs Confirming", None),
        ("Industry rate (confirm)", None),
        ("$90,000/year", None),  # not hourly
        ("", None),
    ],
)
def test_parse_rate(text: str, rate: float | None) -> None:
    assert parse_rate(text) == rate


def test_split_list_drops_blanks_and_duplicates() -> None:
    assert split_list("SEO; content;; SEO.\nCRM", [";", "\n"]) == ["SEO", "content", "CRM"]


def test_cli_writes_csv_and_backs_up_previous(book: Path, tmp_path: Path) -> None:
    out = tmp_path / "contractors.csv"
    out.write_text("old", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["--config-dir", str(CONFIG_DIR), "build-contractors", str(book), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Wrote 3 contractor(s)" in result.output
    assert "Sample Person" not in result.output  # no names in the report
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == (
        "talent_id,talent_name,location,standardized_headline,executive_summary,"
        "core_strengths,proof_points,main_industry,seniority,weekly_hours,rate_expectation,"
        "target_titles,contract"
    )
    assert ",40,52.5," in lines[1]
    assert "Previous file copied to" in result.output
    assert len(list((tmp_path / "backups").glob("contractors.*.csv"))) == 1


def test_missing_tab_is_a_clear_error(tmp_path: Path) -> None:
    book = workbook(tmp_path / "bad.xlsx", {"Talent": TALENT})
    with pytest.raises(ValueError, match="Talent Profiles"):
        build_contractors(read_sheets(book), RULES)
