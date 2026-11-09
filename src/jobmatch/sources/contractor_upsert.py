"""Load a contractor file a person picked and upsert it into the app's own contractor list
(paths.contractors_csv), keyed on talent_id. Nothing is fetched: the file arrives by hand
(for now the master workbook or a CSV export; later the intake form's export).

Accepted files:
- .xlsx: the contractors master workbook, read with build-contractors' rules
  (config/contractor_import.yaml).
- .csv: the contractor list's own columns (ContractorProfile), header and every row checked.
  A row with an empty talent_id gets a new ID (fill_missing_ids): the prefix from
  config/contractor_import.yaml (TAL-CAN-) plus the highest ID number known + 1. A row whose
  name matches a contractor already given such an ID reuses it, so loading the same file
  again makes no duplicate. Names are compared on this machine only and never reported.

Upsert rules (assumptions, to confirm):
- a talent_id not in the list yet is added at the end;
- a known talent_id whose row differs replaces the stored row in place;
- contractors missing from the loaded file are kept (removal needs its own decision).

The list is replaced only when something changed, through the storage backend (with the CSV
backend: atomically, after a timestamped backup in its backups/ folder). Skills are not
learned here. Reports hold counts and talent IDs only, never names.
"""

import csv
import re
import tempfile
from collections.abc import Sequence
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


class LoadedContractors(BaseModel):
    rows: list[ContractorProfile]
    given_ids: list[str] = []  # IDs given to rows that had none (new or reused), in file order


_ID_NUMBER = re.compile(r"(\d+)\s*$")


def _id_number(talent_id: str) -> int:
    found = _ID_NUMBER.search(talent_id)
    return int(found.group(1)) if found else 0


def _name_key(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def fill_missing_ids(
    rows: list[dict[str, str]],
    existing: Sequence[ContractorProfile],
    *,
    prefix: str,
    width: int,
) -> list[str]:
    """Give every row with an empty talent_id an ID, in place, and return the IDs given.

    - A row whose name matches a stored contractor whose ID has this prefix reuses that ID
      (the same file loaded again must not add the person twice).
    - Otherwise: prefix + (highest number among the stored IDs and the file's IDs) + 1,
      counting up, zero-padded to `width` digits. TAL-ON-052 known -> TAL-CAN-053.
    """
    known = [p.talent_id for p in existing]
    known += [(r.get("talent_id") or "").strip() for r in rows]
    next_number = max((_id_number(t) for t in known if t), default=0) + 1
    by_name = {
        _name_key(p.talent_name): p.talent_id for p in existing if p.talent_id.startswith(prefix)
    }
    given: list[str] = []
    for row in rows:
        if (row.get("talent_id") or "").strip():
            continue
        key = _name_key(row.get("talent_name"))
        talent_id = by_name.get(key) if key else None
        if talent_id is None:
            talent_id = f"{prefix}{next_number:0{width}d}"
            next_number += 1
            if key:
                by_name[key] = talent_id
        row["talent_id"] = talent_id
        given.append(talent_id)
    return given


def _read_csv(
    path: Path, existing: Sequence[ContractorProfile], config_dir: Path, lock_timeout: float
) -> LoadedContractors:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    needs_ids = "talent_id" in header and any(not (r.get("talent_id") or "").strip() for r in rows)
    if not needs_ids or any(None in r for r in rows):  # nothing to fill, or a broken row:
        return LoadedContractors(  # the table check reports problems with row numbers
            rows=_table(path, TableMode.READ_ONLY, lock_timeout).read_all()
        )
    rules = load_contractor_import(config_dir)
    given = fill_missing_ids(rows, existing, prefix=rules.new_id_prefix, width=rules.new_id_width)
    # Check the filled rows exactly like any other file: through a private temporary copy
    # with the same name (so messages still name the person's file). The file itself is
    # never changed.
    with tempfile.TemporaryDirectory() as folder:
        filled = Path(folder) / path.name
        with filled.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        loaded = _table(filled, TableMode.READ_ONLY, lock_timeout).read_all()
    return LoadedContractors(rows=loaded, given_ids=given)


def load_contractor_file(
    path: Path,
    *,
    config_dir: Path,
    lock_timeout: float,
    existing: Sequence[ContractorProfile] = (),
) -> LoadedContractors:
    """Read a contractor file. `existing` (the stored list) decides the IDs given to .csv
    rows without one."""
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ContractorFileError(f"not found: {path}")
    try:
        if suffix == ".xlsx":
            rows, _ = build_contractors(read_sheets(path), load_contractor_import(config_dir))
            return LoadedContractors(rows=rows)
        if suffix == ".csv":
            return _read_csv(path, existing, config_dir, lock_timeout)
    except (WorkbookError, CsvStoreError, ValueError, OSError) as exc:
        raise ContractorFileError(f"{path.name}: {exc}") from exc
    raise ContractorFileError(f"{path.name}: expected a .xlsx workbook or a .csv file")


def read_contractor_file(
    path: Path,
    *,
    config_dir: Path,
    lock_timeout: float,
    existing: Sequence[ContractorProfile] = (),
) -> list[ContractorProfile]:
    return load_contractor_file(
        path, config_dir=config_dir, lock_timeout=lock_timeout, existing=existing
    ).rows


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
