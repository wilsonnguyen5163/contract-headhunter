"""CSV storage: typed tables, CSV-backed repositories and human-facing exports.

Conventions (doc/PROJECT_CONTEXT.md section 8a):
- read with utf-8-sig (tolerates Excel's BOM) and newline=""; write UTF-8 with LF.
- headers and every row are validated against a pydantic model; errors name file and row.
- multi-value cells are joined with "|" (a literal "|" or "\\" inside an item is backslash-escaped).
- empty cell = null; booleans true/false; timestamps ISO 8601 UTC; IDs stay strings.
- rewrites are atomic, under a file lock, after a timestamped backup.
- read-only tables (e.g. the built contractor list) are never written.
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
    Application,
    ContractorProfile,
    Job,
    JobStatus,
    LlmMatch,
    LlmUsage,
    Review,
    RoundEvent,
    Run,
    ScanRecord,
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


def is_list_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return True
    if origin is Annotated:
        return is_list_annotation(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return any(is_list_annotation(a) for a in get_args(annotation) if a is not type(None))
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
            name for name, f in model.model_fields.items() if is_list_annotation(f.annotation)
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
                self._upgrade_header()
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

    def rewrite(self, rows: Iterable[M]) -> Path | None:
        """Replace the whole file atomically, after a timestamped backup of the old one.
        Returns the backup's path (None when there was no file yet)."""
        self._require_writable(rewrite=True)
        cells = [self._to_cells(r) for r in rows]
        with self.lock:
            backup = self._backup()
            with atomic_open(self.path) as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(self.columns)
                writer.writerows(cells)
            self._ensure_sidecar()
        return backup

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

    def _read_header(self) -> list[str]:
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return [h.strip() for h in next(csv.reader(f), [])]

    def _upgrade_header(self) -> None:
        """A rewritable file written before optional columns were added (e.g. jobs.csv before
        availability): rewrite it with the current columns, after a timestamped backup, so
        new rows can be appended. Anything else is left for _require_exact_header."""
        if self.mode is not TableMode.READ_WRITE:
            return
        header = self._read_header()
        if header == self.columns or self._header_errors(header):
            return
        self.rewrite(self.read_all())

    def _require_exact_header(self) -> None:
        header = self._read_header()
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
        """Write the sidecar when missing, or refresh its column list after optional columns
        were added (same schema_version: older files still load)."""
        if self.sidecar_path.exists():
            try:
                current = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return  # left for _check_sidecar to report
            if (
                not isinstance(current, dict)
                or current.get("columns") == self.columns
                or current.get("schema_version") != SCHEMA_VERSION
            ):
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

        self.jobs = table("jobs.csv", Job, TableMode.READ_WRITE, ("job_id",))
        self.reviews = table("reviews.csv", Review, TableMode.APPEND_ONLY)
        self.runs = table("runs.csv", Run, TableMode.APPEND_ONLY, ("run_id",))
        # v1: one row per LLM API call (token counts only), read by the daily budget guard.
        self.llm_usage = table("llm_usage.csv", LlmUsage, TableMode.APPEND_ONLY, ("call_id",))
        # v1: Gemini's shortlists (run-once). Rows are only added, never changed.
        self.llm_matches = table("llm_matches.csv", LlmMatch, TableMode.APPEND_ONLY, ("match_id",))
        # 0.4: who was judged for which job, the rounds of recommendations, and the
        # application status of approved candidates. All append-only (history kept).
        self.job_scans = table("job_scans.csv", ScanRecord, TableMode.APPEND_ONLY)
        self.job_rounds = table("job_rounds.csv", RoundEvent, TableMode.APPEND_ONLY)
        self.applications = table("applications.csv", Application, TableMode.APPEND_ONLY)

    def all_tables(self) -> list[CsvTable[Any]]:
        return [
            self.jobs,
            self.reviews,
            self.runs,
            self.llm_usage,
            self.llm_matches,
            self.job_scans,
            self.job_rounds,
            self.applications,
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


class CsvLlmMatchRepository:
    """data/llm_matches.csv (v1). Append-only: re-adding a known match_id does nothing."""

    def __init__(self, store: CsvStore) -> None:
        self._table = store.llm_matches

    def list_all(self) -> list[LlmMatch]:
        return self._table.read_all()

    def add_many(self, matches: Iterable[LlmMatch]) -> list[LlmMatch]:
        with self._table.lock:
            ids = {m.match_id for m in self._table.read_all()}
            added: list[LlmMatch] = []
            for match in matches:
                if match.match_id not in ids:
                    ids.add(match.match_id)
                    added.append(match)
            self._table.append(added)
        return added

    def current(self, judge_keys: Mapping[str, str]) -> dict[str, list[LlmMatch]]:
        """Each job's current shortlist, best first: the rows of the judge_key the job has
        now (job_id -> judge_key). A job judged "no fit" has no rows, so it maps to []."""
        out: dict[str, list[LlmMatch]] = {job_id: [] for job_id in judge_keys}
        for m in self.list_all():
            if judge_keys.get(m.job_id) == m.judge_key:
                out[m.job_id].append(m)
        return {job_id: sorted(rows, key=lambda r: r.rank) for job_id, rows in out.items()}


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
    """data/contractors/contractors.csv, the app's contractor list (paths.contractors_csv):
    built from the workbook (build-contractors) or upserted from a loaded file. Every
    replacement keeps the previous file in data/contractors/backups/ first."""

    def __init__(self, path: Path, *, lock_timeout: float) -> None:
        self._table = CsvTable(
            path,
            ContractorProfile,
            TableMode.READ_WRITE,
            backups_dir=path.parent / "backups",
            lock_timeout=lock_timeout,
            unique_key=("talent_id",),
        )

    @property
    def path(self) -> Path:
        return self._table.path

    def exists(self) -> bool:
        return self._table.path.exists()

    def list_all(self) -> list[ContractorProfile]:
        return self._table.read_all()

    def replace_all(self, profiles: Iterable[ContractorProfile]) -> str | None:
        self._table.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self._table.rewrite(profiles)
        return str(backup) if backup else None


class CsvAppendRepository[M: BaseModel]:
    """An append-only table read whole: job_scans.csv, job_rounds.csv, applications.csv."""

    def __init__(self, table: "CsvTable[M]") -> None:
        self._table = table

    def list_all(self) -> list[M]:
        return self._table.read_all()

    def add_many(self, rows: Iterable[M]) -> int:
        return self._table.append(rows)


class CsvUsageRepository:
    """data/llm_usage.csv (append-only): one row per LLM API call, token counts only."""

    def __init__(self, store: CsvStore) -> None:
        self._table = store.llm_usage

    def record(self, usage: LlmUsage) -> None:
        self._table.append([usage])

    def list_all(self) -> list[LlmUsage]:
        return self._table.read_all()
