from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobmatch.schemas import (
    Band,
    Decision,
    EmploymentType,
    JobStatus,
    Language,
    Match,
    PayUnit,
    ReasonCode,
    Review,
)
from jobmatch.storage.csv_store import (
    AppendOnlyTableError,
    CsvJobRepository,
    CsvReviewRepository,
    CsvValidationError,
    ReadOnlyTableError,
    export_csv,
    join_multi,
    sanitize_cell,
    split_multi,
)

CONTRACTOR_HEADER = (
    "contractor_id,display_name,profile_text_raw,languages,regions,remote_preference,"
    "noc_codes,rate_min,rate_max,rate_unit,rate_currency"
)


def write_raw(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.write_bytes(text.encode(encoding))


# ---- cell codec ----


def test_multi_value_roundtrip_escapes_separator() -> None:
    items = ["python", "C|pipe", "back\\slash", "gestion de projet"]
    assert split_multi(join_multi(items)) == items


def test_split_multi_trims_human_spacing() -> None:
    assert split_multi("python | sql |  aws") == ["python", "sql", "aws"]


def test_join_multi_rejects_empty_items() -> None:
    with pytest.raises(ValueError):
        join_multi(["python", ""])


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ('=HYPERLINK("http://evil")', '\'=HYPERLINK("http://evil")'),
        ("+1 call now", "'+1 call now"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("-0.25", "-0.25"),
        ("+3", "+3"),
        ("Développeur Python", "Développeur Python"),
    ],
)
def test_sanitize_cell(cell: str, expected: str) -> None:
    assert sanitize_cell(cell) == expected


# ---- round trips ----


def test_job_roundtrip_keeps_strings_lists_and_french_text(store, make_job) -> None:
    job = make_job(
        source_id="0042",
        noc_code="00012",
        title_raw="Développeur·se Python principal(e) – contrat",
        company_raw="Banque Exemple Ltée",
        city="Montréal",
        province="CA-QC",
        country="CA",
        employment_type=EmploymentType.CONTRACT,
        contract_signals=["contrat", "durée déterminée"],
        language=Language.FR,
        description_raw='Mandat de 6 mois.\nExpérience en SQL, un atout; "AWS" requis.',
        requirements=["5+ ans en Python", "SQL | PostgreSQL"],
        skills=["python", "sql"],
        pay_min=80.0,
        pay_max=95.5,
        pay_unit=PayUnit.HOUR,
        pay_currency="CAD",
        posted_at=datetime(2026, 9, 30, 8, 15, 30, 123456, tzinfo=UTC),
    )
    store.jobs.append([job])
    assert store.jobs.read_all() == [job]


def test_empty_cell_means_null_and_defaults_apply(store, make_job) -> None:
    store.jobs.append([make_job()])
    loaded = store.jobs.read_all()[0]
    assert loaded.pay_min is None
    assert loaded.skills == []
    assert loaded.status is JobStatus.NEW


def test_reads_excel_bom_crlf_and_leading_zero_ids(store) -> None:
    text = CONTRACTOR_HEADER + "\r\n007,Sample Person,,en|fr,CA-ON,remote,00012,,,,\r\n"
    write_raw(store.contractors.path, text, encoding="utf-8-sig")
    [contractor] = store.contractors.read_all()
    assert contractor.contractor_id == "007"
    assert contractor.noc_codes == ["00012"]
    assert contractor.languages == ["en", "fr"]


# ---- validation errors name file and row ----


def test_unknown_column_is_reported_with_file_name(store) -> None:
    write_raw(store.contractors.path, CONTRACTOR_HEADER + ",email\nC1,A,,,,,,,,,,a@example.org\n")
    _, errors = store.contractors.check()
    assert len(errors) == 1
    assert "contractors.csv, row 1" in str(errors[0])
    assert "'email'" in str(errors[0])


def test_missing_required_column_is_reported(store) -> None:
    write_raw(store.contractor_facets.path, "contractor_id,area\nC1,Data\n")
    _, errors = store.contractor_facets.check()
    assert any("facet_id" in str(e) for e in errors)


def test_bad_row_reports_row_number_and_keeps_good_rows(store) -> None:
    text = CONTRACTOR_HEADER + "\nC1,A,,,,,21231,,,,\nC2,B,,,,,123,,,,\n"
    write_raw(store.contractors.path, text)
    rows, errors = store.contractors.check()
    assert [r.contractor_id for r in rows] == ["C1"]
    assert len(errors) == 1
    assert errors[0].row == 3
    assert "noc_codes" in errors[0].message
    with pytest.raises(CsvValidationError, match=r"contractors\.csv, row 3"):
        store.contractors.read_all()


def test_validation_errors_do_not_echo_cell_values(store) -> None:
    text = CONTRACTOR_HEADER + "\nC1,Secret Name,,,,,,not-a-number,,,\n"
    write_raw(store.contractors.path, text)
    _, errors = store.contractors.check()
    assert errors
    assert all("Secret Name" not in str(e) for e in errors)


def test_duplicate_key_is_reported(store) -> None:
    text = "contractor_id,facet_id,area\nC1,data,Data\nC1,data,Data again\n"
    write_raw(store.contractor_facets.path, text)
    _, errors = store.contractor_facets.check()
    assert len(errors) == 1
    assert errors[0].row == 3
    assert "duplicate" in errors[0].message


def test_blank_lines_are_ignored(store) -> None:
    write_raw(store.contractor_facets.path, "contractor_id,facet_id,area\n\nC1,data,Data\n,,\n")
    rows, errors = store.contractor_facets.check()
    assert errors == []
    assert len(rows) == 1


# ---- write rules ----


def test_human_maintained_tables_are_never_written(store) -> None:
    with pytest.raises(ReadOnlyTableError):
        store.contractors.append([])
    with pytest.raises(ReadOnlyTableError):
        store.contractor_facets.rewrite([])


def test_reviews_are_append_only(store) -> None:
    with pytest.raises(AppendOnlyTableError):
        store.reviews.rewrite([])


def test_rewrite_takes_backup_and_leaves_no_temp_files(store, make_job) -> None:
    repo = CsvJobRepository(store)
    repo.add_new([make_job()])
    original = store.jobs.path.read_bytes()

    repo.update_statuses({"adzuna:0042": (JobStatus.FILTERED_OUT, "not_canada")})

    backups = list(store.backups_dir.glob("jobs.*.csv"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert repo.get("adzuna:0042").status is JobStatus.FILTERED_OUT
    assert not list(store.data_dir.glob("*.tmp"))


def test_update_statuses_unknown_id_writes_nothing(store, make_job) -> None:
    repo = CsvJobRepository(store)
    repo.add_new([make_job()])
    with pytest.raises(KeyError):
        repo.update_statuses({"missing": (JobStatus.MATCHED, None)})
    assert not store.backups_dir.exists()


def test_append_refuses_mismatched_header(store, make_job) -> None:
    write_raw(store.jobs.path, "job_id,source\nx,y\n")
    with pytest.raises(CsvValidationError):
        store.jobs.append([make_job()])


def test_internal_files_use_lf_and_no_bom(store, make_job) -> None:
    store.jobs.append([make_job()])
    raw = store.jobs.path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_schema_sidecar_written_and_checked(store, make_job) -> None:
    store.jobs.append([make_job()])
    assert store.jobs.sidecar_path.exists()
    store.jobs.sidecar_path.write_text('{"schema_version": 99}', encoding="utf-8")
    _, errors = store.jobs.check()
    assert any("schema_version 99" in str(e) for e in errors)


# ---- repositories ----


def test_add_new_dedupes_on_source_and_source_id(store, make_job) -> None:
    repo = CsvJobRepository(store)
    assert len(repo.add_new([make_job()])) == 1
    again = make_job(job_id="adzuna:0042-copy")
    assert repo.add_new([again, again]) == []
    assert len(repo.list_all()) == 1


def test_latest_review_wins(store) -> None:
    repo = CsvReviewRepository(store)
    first = Review(
        match_id="m1",
        decision=Decision.NEEDS_INFO,
        reviewer="reviewer1",
        reviewed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )
    second = Review(
        match_id="m1",
        decision=Decision.REJECTED,
        reason_code=ReasonCode.NOT_CONTRACT,
        notes="Poste permanent, pas un contrat.",
        reviewer="reviewer1",
        reviewed_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    )
    repo.append(first)
    repo.append(second)
    assert repo.latest_by_match()["m1"] == second
    assert len(repo.list_all()) == 2


def test_rejection_requires_reason_code() -> None:
    with pytest.raises(ValidationError):
        Review(
            match_id="m1",
            decision=Decision.REJECTED,
            reviewer="reviewer1",
            reviewed_at=datetime(2026, 10, 1, tzinfo=UTC),
        )


# ---- exports ----


def test_export_has_bom_and_neutralizes_formulas(tmp_path) -> None:
    match = Match(
        match_id="m1",
        job_id="=cmd|' /C calc'!A0",
        contractor_id="C1",
        facet="data_engineering",
        cosine=-0.25,
        skill_overlap=0.5,
        score=0.4,
        band=Band.LOW,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    dest = tmp_path / "exports" / "matches.csv"
    assert export_csv(Match, [match], dest) == 1
    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "'=cmd" in text
    assert ",-0.25," in text
