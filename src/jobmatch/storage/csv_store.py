"""CSV storage: typed tables, CSV-backed repositories and human-facing exports.

Conventions (PROJECT_CONTEXT.md section 8a):
- read with utf-8-sig (tolerates Excel's BOM) and newline=""; write UTF-8 with LF.
- headers and every row are validated against a pydantic model; errors name file and row.
- multi-value cells are joined with "|" (a literal "|" or "\\" inside an item is backslash-escaped).
- empty cell = null; booleans true/false; timestamps ISO 8601 UTC; IDs stay strings.
- rewrites are atomic, under a file lock, after a timestamped backup.
- contractors.csv and contractor_facets.csv are human-maintained: never written.
- reviews.csv and runs.csv are append-only.
- human-facing exports neutralize spreadsheet formulas (posting text is untrusted).
"""

import csv
import json
import os
import re
import shutil
import types
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Annotated, Any, Union, get_args, get_origin

from filelock import FileLock
from pydantic import BaseModel, ValidationError

from jobmatch.schemas import (
    SCHEMA_VERSION,
    Contractor,
    ContractorFacet,
    ContractorProfile,
    Job,
    JobStatus,
    Match,
    Review,
    Run,
)
from jobmatch.storage.files import atomic_open, atomic_write_text

MULTI_SEPARATOR = "|"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


# ---- errors ----


class CsvError(BaseModel):
    file: str
    row: int | None = None  # spreadsheet row number: the header is row 1
    message: str

    def __str__(self) -> str:
        where = self.file if self.row is None else f"{self.file}, row {self.row}"
        return f"{where}: {self.message}"


class CsvStoreError(Exception):
    pass


class CsvValidationError(CsvStoreError):
    def __init__(self, errors: Sequence[CsvError]) -> None:
        self.errors = list(errors)
        shown = "\n".join(str(e) for e in self.errors[:20])
        extra = len(self.errors) - 20
        super().__init__(shown + (f"\n... and {extra} more" if extra > 0 else ""))


class ReadOnlyTableError(CsvStoreError):
    pass


class AppendOnlyTableError(CsvStoreError):
    pass


# ---- cell codec ----


def join_multi(items: Iterable[str]) -> str:
    escaped = []
    for item in items:
        if item == "":
            raise ValueError("multi-value cells cannot contain empty items")
        escaped.append(item.replace("\\", "\\\\").replace(MULTI_SEPARATOR, "\\" + MULTI_SEPARATOR))
    return MULTI_SEPARATOR.join(escaped)


def split_multi(cell: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(cell):
        char = cell[i]
        if char == "\\" and i + 1 < len(cell):
            buf.append(cell[i + 1])
            i += 2
            continue
        if char == MULTI_SEPARATOR:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(char)
        i += 1
    items.append("".join(buf))
    return [item.strip() for item in items if item.strip()]


def to_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, list | tuple):
        return join_multi(to_cell(v) for v in value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)  # 40, not 40.0
    return str(value)


def sanitize_cell(cell: str) -> str:
    """Neutralize spreadsheet formulas in human-facing exports (numbers are left alone)."""
    if cell.startswith(_FORMULA_PREFIXES) and not _NUMERIC.match(cell):
        return "'" + cell
    return cell


def _is_list_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return True
    if origin is Annotated:
        return _is_list_annotation(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return any(_is_list_annotation(a) for a in get_args(annotation) if a is not type(None))
    return False


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            return True
        f.seek(-1, os.SEEK_END)
        return f.read(1) in (b"\n", b"\r")


# ---- tables ----


class TableMode(StrEnum):
    READ_ONLY = "read_only"
    APPEND_ONLY = "append_only"
    READ_WRITE = "read_write"


class CsvTable[M: BaseModel]:
    """One CSV file whose rows are instances of `model`."""

    def __init__(
        self,
        path: Path,
        model: type[M],
        mode: TableMode,
        *,
        backups_dir: Path,
        lock_timeout: float,
        unique_key: tuple[str, ...] = (),
    ) -> None:
        self.path = path
        self.model = model
        self.mode = mode
        self.backups_dir = backups_dir
        self.unique_key = unique_key
        self.columns = list(model.model_fields)
        self._list_fields = {
            name for name, f in model.model_fields.items() if _is_list_annotation(f.annotation)
        }
        self._required = {name for name, f in model.model_fields.items() if f.is_required()}
        # Reentrant within this object, so callers can hold it around read + write.
        self.lock = FileLock(f"{path}.lock", timeout=lock_timeout)

    @property
    def sidecar_path(self) -> Path:
        return self.path.with_name(f"{self.path.stem}.schema.json")

    # -- reading --

    def check(self) -> tuple[list[M], list[CsvError]]:
        """Parse the file and return (valid rows, every problem found). Missing file = empty."""
        if not self.path.exists():
            return [], []
        with self._read_lock():
            rows, errors = self._parse()
        errors.extend(self._check_sidecar())
        return rows, errors

    def read_all(self) -> list[M]:
        rows, errors = self.check()
        if errors:
            raise CsvValidationError(errors)
        return rows

    def _read_lock(self) -> AbstractContextManager[Any]:
        # Human-maintained files are never written by jobmatch, so reading needs no lock file.
        return nullcontext() if self.mode is TableMode.READ_ONLY else self.lock

    def _parse(self) -> tuple[list[M], list[CsvError]]:
        name = self.path.name
        rows: list[M] = []
        errors: list[CsvError] = []
        seen_keys: dict[tuple[Any, ...], int] = {}
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader, None)
                if header is None:
                    return [], [CsvError(file=name, row=1, message="file is empty (no header row)")]
                header = [h.strip() for h in header]
                header_errors = self._header_errors(header)
                if header_errors:
                    return [], header_errors
                for row_no, cells in enumerate(reader, start=2):
                    if not any(c.strip() for c in cells):
                        continue
                    if len(cells) != len(header):
                        errors.append(
                            CsvError(
                                file=name,
                                row=row_no,
                                message=f"expected {len(header)} cells, found {len(cells)}",
                            )
                        )
                        continue
                    try:
                        row = self._from_cells(dict(zip(header, cells, strict=True)))
                    except ValidationError as exc:
                        for err in exc.errors(include_input=False, include_url=False):
                            field = ".".join(str(p) for p in err["loc"]) or "row"
                            errors.append(
                                CsvError(file=name, row=row_no, message=f"{field}: {err['msg']}")
                            )
                        continue
                    if self.unique_key:
                        key = tuple(getattr(row, k) for k in self.unique_key)
                        if key in seen_keys:
                            errors.append(
                                CsvError(
                                    file=name,
                                    row=row_no,
                                    message=f"duplicate {'/'.join(self.unique_key)} "
                                    f"(first seen in row {seen_keys[key]})",
                                )
                            )
                            continue
                        seen_keys[key] = row_no
                    rows.append(row)
            except csv.Error as exc:
                message = f"CSV syntax error near line {reader.line_num}: {exc}"
                errors.append(CsvError(file=name, message=message))
        return rows, errors

    def _header_errors(self, header: list[str]) -> list[CsvError]:
        name = self.path.name
        errors: list[CsvError] = []
        duplicates = sorted({h for h in header if header.count(h) > 1})
        unknown = [h for h in header if h not in self.model.model_fields]
        missing = sorted(self._required - set(header))
        if duplicates:
            errors.append(
                CsvError(file=name, row=1, message=f"duplicate column(s): {', '.join(duplicates)}")
            )
        if unknown:
            errors.append(
                CsvError(
                    file=name,
                    row=1,
                    message=f"unknown column(s): {', '.join(repr(h) for h in unknown)}; "
                    f"expected columns: {', '.join(self.columns)}",
                )
            )
        if missing:
            errors.append(
                CsvError(
                    file=name, row=1, message=f"missing required column(s): {', '.join(missing)}"
                )
            )
        return errors

    def _from_cells(self, raw: Mapping[str, str]) -> M:
        data: dict[str, Any] = {}
        for name, cell in raw.items():
            if not cell.strip():
                continue  # empty cell = null: the field default applies
            data[name] = split_multi(cell) if name in self._list_fields else cell
        return self.model.model_validate(data)

    def _to_cells(self, row: M) -> list[str]:
        if not isinstance(row, self.model):
            raise TypeError(
                f"{self.path.name} stores {self.model.__name__}, got {type(row).__name__}"
            )
        return [to_cell(getattr(row, column)) for column in self.columns]

    # -- writing --

    def append(self, rows: Iterable[M]) -> int:
        """Append rows (creating the file if needed). Returns the number written."""
        self._require_writable(rewrite=False)
        cells = [self._to_cells(r) for r in rows]
        if not cells:
            return 0
        with self.lock:
            if not self.path.exists():
                with atomic_open(self.path) as f:
                    writer = csv.writer(f, lineterminator="\n")
                    writer.writerow(self.columns)
                    writer.writerows(cells)
            else:
                self._require_exact_header()
                needs_newline = not _ends_with_newline(self.path)
                with self.path.open("a", encoding="utf-8", newline="") as f:
                    if needs_newline:
                        f.write("\n")
                    csv.writer(f, lineterminator="\n").writerows(cells)
                    f.flush()
                    os.fsync(f.fileno())
            self._ensure_sidecar()
        return len(cells)

    def rewrite(self, rows: Iterable[M]) -> None:
        """Replace the whole file atomically, after a timestamped backup of the old one."""
        self._require_writable(rewrite=True)
        cells = [self._to_cells(r) for r in rows]
        with self.lock:
            self._backup()
            with atomic_open(self.path) as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(self.columns)
                writer.writerows(cells)
            self._ensure_sidecar()

    def update(self, change: Callable[[list[M]], list[M]]) -> list[M]:
        """Read-modify-write under one lock. `change` may raise to abort without writing."""
        self._require_writable(rewrite=True)
        with self.lock:
            new_rows = change(self.read_all())
            self.rewrite(new_rows)
        return new_rows

    def _require_writable(self, *, rewrite: bool) -> None:
        if self.mode is TableMode.READ_ONLY:
            raise ReadOnlyTableError(
                f"{self.path.name} is human-maintained; jobmatch never writes it"
            )
        if rewrite and self.mode is TableMode.APPEND_ONLY:
            raise AppendOnlyTableError(f"{self.path.name} is append-only")

    def _require_exact_header(self) -> None:
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            header = [h.strip() for h in next(csv.reader(f), [])]
        if header != self.columns:
            raise CsvValidationError(
                [
                    CsvError(
                        file=self.path.name,
                        row=1,
                        message="header does not match the current schema; run validate-data",
                    )
                ]
            )

    def _backup(self) -> Path | None:
        if not self.path.exists():
            return None
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        dest = self.backups_dir / f"{self.path.stem}.{stamp}{self.path.suffix}"
        shutil.copy2(self.path, dest)
        return dest

    # -- schema sidecar --

    def _ensure_sidecar(self) -> None:
        if self.sidecar_path.exists():
            return
        payload = {
            "schema_version": SCHEMA_VERSION,
            "model": self.model.__name__,
            "columns": self.columns,
        }
        atomic_write_text(self.sidecar_path, json.dumps(payload, indent=2) + "\n")

    def _check_sidecar(self) -> list[CsvError]:
        if not self.sidecar_path.exists():
            return []
        try:
            version = json.loads(self.sidecar_path.read_text(encoding="utf-8"))["schema_version"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [CsvError(file=self.sidecar_path.name, message=f"unreadable sidecar: {exc}")]
        if version != SCHEMA_VERSION:
            return [
                CsvError(
                    file=self.path.name,
                    message=f"written with schema_version {version}, code expects "
                    f"{SCHEMA_VERSION}; migrate before use",
                )
            ]
        return []


# ---- store ----


class CsvStore:
    """All CSV tables under one data folder."""

    def __init__(self, data_dir: Path, *, lock_timeout: float) -> None:
        self.data_dir = data_dir
        self.backups_dir = data_dir / "backups"

        def table[T: BaseModel](
            filename: str, model: type[T], mode: TableMode, key: tuple[str, ...] = ()
        ) -> CsvTable[T]:
            return CsvTable(
                data_dir / filename,
                model,
                mode,
                backups_dir=self.backups_dir,
                lock_timeout=lock_timeout,
                unique_key=key,
            )

        self.contractors = table(
            "contractors.csv", Contractor, TableMode.READ_ONLY, ("contractor_id",)
        )
        self.contractor_facets = table(
            "contractor_facets.csv",
            ContractorFacet,
            TableMode.READ_ONLY,
            ("contractor_id", "facet_id"),
        )
        self.jobs = table("jobs.csv", Job, TableMode.READ_WRITE, ("job_id",))
        self.matches = table("matches.csv", Match, TableMode.READ_WRITE, ("match_id",))
        self.matches_flagged = table(
            "matches_flagged.csv", Match, TableMode.READ_WRITE, ("match_id",)
        )
        self.reviews = table("reviews.csv", Review, TableMode.APPEND_ONLY)
        self.runs = table("runs.csv", Run, TableMode.APPEND_ONLY, ("run_id",))

    def all_tables(self) -> list[CsvTable[Any]]:
        return [
            self.contractors,
            self.contractor_facets,
            self.jobs,
            self.matches,
            self.matches_flagged,
            self.reviews,
            self.runs,
        ]


def export_csv[M: BaseModel](model: type[M], rows: Iterable[M], dest: Path) -> int:
    """Write a human-facing copy (UTF-8 with BOM for Excel, formulas neutralized)."""
    columns = list(model.model_fields)
    count = 0
    with atomic_open(dest, encoding="utf-8-sig") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([sanitize_cell(to_cell(getattr(row, c))) for c in columns])
            count += 1
    return count


# ---- CSV-backed repositories ----


class CsvJobRepository:
    def __init__(self, store: CsvStore) -> None:
        self._table = store.jobs

    def list_all(self) -> list[Job]:
        return self._table.read_all()

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.list_all() if j.job_id == job_id), None)

    def add_new(self, jobs: Iterable[Job]) -> list[Job]:
        """Append jobs not already stored; uniqueness on (source, source_id) and on job_id."""
        with self._table.lock:
            existing = self._table.read_all()
            keys = {(j.source, j.source_id) for j in existing}
            ids = {j.job_id for j in existing}
            added: list[Job] = []
            for job in jobs:
                key = (job.source, job.source_id)
                if key in keys or job.job_id in ids:
                    continue
                keys.add(key)
                ids.add(job.job_id)
                added.append(job)
            self._table.append(added)
        return added

    def update_statuses(self, updates: Mapping[str, tuple[JobStatus, str | None]]) -> None:
        """Set (status, filter_reason) for several jobs in one atomic rewrite."""

        def apply(rows: list[Job]) -> list[Job]:
            unknown = set(updates) - {j.job_id for j in rows}
            if unknown:
                raise KeyError(f"unknown job_id(s): {', '.join(sorted(unknown))}")
            out = []
            for job in rows:
                if job.job_id in updates:
                    status, reason = updates[job.job_id]
                    job = job.model_copy(update={"status": status, "filter_reason": reason})
                out.append(job)
            return out

        if updates:
            self._table.update(apply)

    def replace(self, jobs: Iterable[Job]) -> None:
        """Replace stored rows (matched by job_id) in one atomic rewrite, after a backup."""
        replacements = {job.job_id: job for job in jobs}

        def apply(rows: list[Job]) -> list[Job]:
            unknown = set(replacements) - {j.job_id for j in rows}
            if unknown:
                raise KeyError(f"unknown job_id(s): {', '.join(sorted(unknown))}")
            return [replacements.get(job.job_id, job) for job in rows]

        if replacements:
            self._table.update(apply)


class CsvRunRepository:
    def __init__(self, store: CsvStore) -> None:
        self._table = store.runs

    def append(self, run: Run) -> None:
        self._table.append([run])

    def list_all(self) -> list[Run]:
        return self._table.read_all()


class CsvContractorRepository:
    def __init__(self, store: CsvStore) -> None:
        self._contractors = store.contractors
        self._facets = store.contractor_facets

    def list_contractors(self) -> list[Contractor]:
        return self._contractors.read_all()

    def list_facets(self) -> list[ContractorFacet]:
        return self._facets.read_all()


class CsvMatchRepository:
    """matches.csv, or with flagged=True matches_flagged.csv (a rule check failed)."""

    def __init__(self, store: CsvStore, *, flagged: bool = False) -> None:
        self._table = store.matches_flagged if flagged else store.matches

    def list_all(self) -> list[Match]:
        return self._table.read_all()

    def list_for_job(self, job_id: str) -> list[Match]:
        return [m for m in self.list_all() if m.job_id == job_id]

    def add_many(self, matches: Iterable[Match]) -> list[Match]:
        """Append matches whose match_id is not stored yet (safe to re-run)."""
        with self._table.lock:
            ids = {m.match_id for m in self._table.read_all()}
            added: list[Match] = []
            for match in matches:
                if match.match_id not in ids:
                    ids.add(match.match_id)
                    added.append(match)
            self._table.append(added)
        return added


class CsvReviewRepository:
    def __init__(self, store: CsvStore) -> None:
        self._table = store.reviews

    def append(self, review: Review) -> None:
        self._table.append([review])

    def list_all(self) -> list[Review]:
        return self._table.read_all()

    def latest_by_match(self) -> dict[str, Review]:
        """Current decision per match: the latest review (later rows win ties)."""
        latest: dict[str, Review] = {}
        for review in self.list_all():
            current = latest.get(review.match_id)
            if current is None or review.reviewed_at >= current.reviewed_at:
                latest[review.match_id] = review
        return latest


class CsvContractorProfileRepository:
    """Read-only access to data/contractors/contractors.csv (built by build-contractors)."""

    def __init__(self, path: Path, *, lock_timeout: float) -> None:
        self._table = CsvTable(
            path,
            ContractorProfile,
            TableMode.READ_ONLY,
            backups_dir=path.parent / "backups",
            lock_timeout=lock_timeout,
            unique_key=("talent_id",),
        )

    @property
    def path(self) -> Path:
        return self._table.path

    def list_all(self) -> list[ContractorProfile]:
        return self._table.read_all()
