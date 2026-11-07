"""Loading a contractor file into the app's contractor list (upsert on talent_id)."""

from pathlib import Path

import pytest

from conftest import CONFIG_DIR
from jobmatch.schemas import ContractorProfile
from jobmatch.sources.contractor_upsert import (
    ContractorFileError,
    read_contractor_file,
    upsert_contractors,
)
from jobmatch.storage.csv_store import CsvContractorProfileRepository


def person(talent_id: str, headline: str, **extra: object) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id,
        talent_name=f"Person {talent_id}",
        standardized_headline=headline,
        rate_expectation="Industry Standard",
        **extra,  # type: ignore[arg-type]
    )


def repo(path: Path) -> CsvContractorProfileRepository:
    return CsvContractorProfileRepository(path, lock_timeout=5)


def stored(path: Path) -> list[ContractorProfile]:
    return CsvContractorProfileRepository(path, lock_timeout=5).list_all()


def backups(path: Path) -> list[Path]:
    folder = path.parent / "backups"
    return sorted(folder.glob("contractors.*.csv")) if folder.exists() else []


def test_first_load_creates_the_list(tmp_path: Path) -> None:
    dest = tmp_path / "contractors" / "contractors.csv"
    report = upsert_contractors(
        [person("TAL-1", "Data Analyst"), person("TAL-2", "Analyste de données")],
        repo(dest),
    )  # fmt: skip
    assert (report.added, report.updated, report.unchanged) == (["TAL-1", "TAL-2"], [], 0)
    assert [p.standardized_headline for p in stored(dest)] == [
        "Data Analyst", "Analyste de données",
    ]  # fmt: skip
    assert backups(dest) == []  # nothing to back up the first time


def test_new_rows_added_changed_rows_updated_others_kept(tmp_path: Path) -> None:
    dest = tmp_path / "contractors.csv"
    upsert_contractors(
        [person("TAL-1", "Data Analyst"), person("TAL-2", "Recruiter"),
         person("TAL-3", "Rédactrice UX", languages=["fr"])],
        repo(dest),
    )  # fmt: skip
    report = upsert_contractors(
        [person("TAL-2", "Senior Recruiter"), person("TAL-3", "Rédactrice UX", languages=["fr"]),
         person("TAL-4", "Gestionnaire de projet")],
        repo(dest),
    )  # fmt: skip
    assert report.added == ["TAL-4"] and report.updated == ["TAL-2"]
    assert (report.unchanged, report.kept_not_in_file, report.file_rows) == (1, 1, 3)
    assert [(p.talent_id, p.standardized_headline) for p in stored(dest)] == [
        ("TAL-1", "Data Analyst"), ("TAL-2", "Senior Recruiter"),
        ("TAL-3", "Rédactrice UX"), ("TAL-4", "Gestionnaire de projet"),
    ]  # fmt: skip
    assert len(backups(dest)) == 1


def test_loading_the_same_file_again_changes_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "contractors.csv"
    rows = [person("TAL-1", "Data Analyst")]
    upsert_contractors(rows, repo(dest))
    before = dest.read_bytes()
    report = upsert_contractors(rows, repo(dest))
    assert not report.changed and report.unchanged == 1
    assert dest.read_bytes() == before and backups(dest) == []


def test_duplicate_talent_ids_in_the_file_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ContractorFileError, match="TAL-1"):
        upsert_contractors([person("TAL-1", "A"), person("TAL-1", "B")], repo(tmp_path / "c.csv"))


def test_csv_file_is_read_and_checked(tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    upsert_contractors([person("TAL-9", "Chargée de communication")], repo(good))
    rows = read_contractor_file(good, config_dir=CONFIG_DIR, lock_timeout=5)
    assert [r.talent_id for r in rows] == ["TAL-9"]

    bad = tmp_path / "bad.csv"
    bad.write_text("talent_id,nickname\nTAL-1,x\n", encoding="utf-8")
    with pytest.raises(ContractorFileError, match="bad.csv"):
        read_contractor_file(bad, config_dir=CONFIG_DIR, lock_timeout=5)


def test_other_file_types_and_missing_files_are_refused(tmp_path: Path) -> None:
    other = tmp_path / "people.txt"
    other.write_text("TAL-1", encoding="utf-8")
    with pytest.raises(ContractorFileError, match=".xlsx"):
        read_contractor_file(other, config_dir=CONFIG_DIR, lock_timeout=5)
    with pytest.raises(ContractorFileError, match="not found"):
        read_contractor_file(tmp_path / "nope.csv", config_dir=CONFIG_DIR, lock_timeout=5)
