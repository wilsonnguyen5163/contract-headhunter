"""Load a contractor file a person picked and upsert it into the app's own contractor list
(paths.contractors_csv), keyed on talent_id. Nothing is fetched: the file arrives by hand
(for now the master workbook or a CSV export; later the intake form's export).

Accepted files:
- .xlsx: the contractors master workbook, read with build-contractors' rules
  (config/contractor_import.yaml).
- .csv: the contractor list's own columns (ContractorProfile), header and every row checked.

Upsert rules (assumptions, to confirm):
- a talent_id not in the list yet is added at the end;
- a known talent_id whose row differs replaces the stored row in place;
- contractors missing from the loaded file are kept (removal needs its own decision).

The list is replaced only when something changed, through the storage backend (with the CSV
backend: atomically, after a timestamped backup in its backups/ folder). Skills are not
learned here. Reports hold counts and talent IDs only, never names.
"""

from pathlib import Path

from pydantic import BaseModel

from jobmatch.schemas import ContractorProfile
from jobmatch.sources.contractor_sheet import build_contractors, load_contractor_import
from jobmatch.sources.xlsx import WorkbookError, read_sheets
from jobmatch.storage.csv_store import CsvStoreError, CsvTable, TableMode
from jobmatch.storage.repositories import ContractorProfileRepository


class ContractorFileError(Exception):
    pass


class UpsertReport(BaseModel):
    file_rows: int
    added: list[str]  # talent IDs
    updated: list[str]
    unchanged: int
    kept_not_in_file: int

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated)


def _table(path: Path, mode: TableMode, lock_timeout: float) -> CsvTable[ContractorProfile]:
    return CsvTable(
        path,
        ContractorProfile,
        mode,
        backups_dir=path.parent / "backups",
        lock_timeout=lock_timeout,
        unique_key=("talent_id",),
    )


def read_contractor_file(
    path: Path, *, config_dir: Path, lock_timeout: float
) -> list[ContractorProfile]:
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ContractorFileError(f"not found: {path}")
    try:
        if suffix == ".xlsx":
            rows, _ = build_contractors(read_sheets(path), load_contractor_import(config_dir))
            return rows
        if suffix == ".csv":
            return _table(path, TableMode.READ_ONLY, lock_timeout).read_all()
    except (WorkbookError, CsvStoreError, ValueError, OSError) as exc:
        raise ContractorFileError(f"{path.name}: {exc}") from exc
    raise ContractorFileError(f"{path.name}: expected a .xlsx workbook or a .csv file")


def upsert_contractors(
    incoming: list[ContractorProfile], repo: ContractorProfileRepository
) -> UpsertReport:
    """Merge `incoming` into the stored contractor list (see the module docstring). The list
    is replaced only when something changed, and the repository keeps the old one first.
    Run under the pipeline lock, so nothing else writes the list meanwhile."""
    ids = [p.talent_id for p in incoming]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ContractorFileError(f"talent_id listed more than once: {', '.join(dupes)}")
    by_id = {p.talent_id: p for p in incoming}
    stored = repo.list_all() if repo.exists() else []
    stored_ids = {p.talent_id for p in stored}
    updated: list[str] = []
    merged: list[ContractorProfile] = []
    for profile in stored:
        new = by_id.get(profile.talent_id)
        if new is not None and new != profile:
            updated.append(profile.talent_id)
            merged.append(new)
        else:
            merged.append(profile)
    added = [p for p in incoming if p.talent_id not in stored_ids]
    merged += added
    report = UpsertReport(
        file_rows=len(incoming),
        added=[p.talent_id for p in added],
        updated=updated,
        unchanged=len(incoming) - len(added) - len(updated),
        kept_not_in_file=len(stored_ids - set(by_id)),
    )
    if report.changed:
        repo.replace_all(merged)  # keeps the previous list first
    return report
