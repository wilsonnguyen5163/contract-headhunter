"""Contractor folders and résumés (user decision 2026-11-09).

The reviewer picks one folder that holds the contractor data:

    <folder>/
      contractors.csv        exactly one contractor file (.xlsx workbook or .csv), any name
      resume/                optional; one PDF per contractor, named by talent ID
        TAL-CAN-0001.pdf

"resume" or "resumes", in any capitalisation. A PDF whose name is not a known talent ID is
skipped and reported (by file name, on screen only: a file name may hold a person's name, so
it never goes into logs or run reports).

Résumés are copied into the app's own folder (data/contractors/resumes/<talent_id>.pdf), so
the app keeps working when the picked folder moves. Copies are atomic. An identical file is
left alone; a changed one replaces the stored copy after the old one is moved to old/ with a
timestamp, so nothing is lost. Résumés are only shown to the reviewer: they are never sent to
Gemini and do not change the profile (no rescan). Their content is never read (only the first
bytes, to check it is a PDF), so nothing in contractors.csv is ever inferred from a résumé
(user decision 2026-11-09; tests/test_resumes.py checks it).
"""

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from jobmatch.sources.contractor_upsert import ContractorFileError

CONTRACTOR_SUFFIXES = (".xlsx", ".csv")
RESUME_FOLDER_NAMES = ("resume", "resumes")
RESUME_SUFFIX = ".pdf"
PDF_MAGIC = b"%PDF-"


class ContractorFolderError(ContractorFileError):
    """A problem with the picked folder, in plain words (no contractor data)."""


class ContractorFolder(BaseModel):
    contractor_file: Path
    resumes: dict[str, Path]  # file stem (the talent ID, if named right) -> PDF


class ResumeReport(BaseModel):
    """Counts only: unmatched file names stay on screen (see the module docstring)."""

    added: int = 0
    replaced: int = 0
    unchanged: int = 0
    unmatched: int = 0


def resumes_dir(data_dir: Path) -> Path:
    return data_dir / "contractors" / "resumes"


def _visible(path: Path) -> bool:
    # Hidden files, macOS "._" forks and Excel's "~$" lock files are not the reviewer's.
    return not path.name.startswith((".", "~$"))


def read_contractor_folder(folder: Path) -> ContractorFolder:
    if not folder.is_dir():
        raise ContractorFolderError(f"not a folder: {folder.name}")
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and _visible(p) and p.suffix.lower() in CONTRACTOR_SUFFIXES
    )  # fmt: skip
    if not files:
        raise ContractorFolderError(
            f"{folder.name}: no contractor file (.xlsx or .csv) at the top of the folder"
        )
    if len(files) > 1:
        names = ", ".join(p.name for p in files)
        raise ContractorFolderError(
            f"{folder.name}: more than one contractor file ({names}); keep only one"
        )
    subfolders = [
        p for p in folder.iterdir() if p.is_dir() and p.name.lower() in RESUME_FOLDER_NAMES
    ]
    if len(subfolders) > 1:
        raise ContractorFolderError(
            f"{folder.name}: both {' and '.join(sorted(p.name for p in subfolders))}; keep one"
        )
    return ContractorFolder(
        contractor_file=files[0],
        resumes=resume_files(subfolders[0].iterdir()) if subfolders else {},
    )


def resume_files(paths: Iterable[Path]) -> dict[str, Path]:
    """PDFs keyed by file stem (later duplicates of a stem are ignored)."""
    found: dict[str, Path] = {}
    for p in sorted(paths):
        if p.is_file() and _visible(p) and p.suffix.lower() == RESUME_SUFFIX:
            found.setdefault(p.stem.strip(), p)
    return found


def match_ids(stems: Iterable[str], known: Iterable[str]) -> tuple[dict[str, str], list[str]]:
    """({stem: talent ID}, unmatched stems). A stem matches an ID ignoring case."""
    by_upper = {k.upper(): k for k in known}
    matched, unmatched = {}, []
    for stem in stems:
        talent_id = by_upper.get(stem.upper())
        if talent_id is None:
            unmatched.append(stem)
        else:
            matched[stem] = talent_id
    return matched, unmatched


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_atomic(source: Path, dest: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store_resumes(
    resumes: Mapping[str, Path], known_ids: Iterable[str], data_dir: Path, now: datetime
) -> ResumeReport:
    """Copy each résumé whose stem is a known talent ID into the app's résumé folder."""
    matched, unmatched = match_ids(resumes, known_ids)
    report = ResumeReport(unmatched=len(unmatched))
    if not matched:
        return report
    folder = resumes_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for stem, talent_id in matched.items():
        source = resumes[stem]
        with source.open("rb") as fh:
            if fh.read(len(PDF_MAGIC)) != PDF_MAGIC:
                report.unmatched += 1  # not a real PDF: skipped like a misnamed one
                continue
        dest = folder / f"{talent_id}{RESUME_SUFFIX}"
        if dest.exists():
            if _digest(dest) == _digest(source):
                report.unchanged += 1
                continue
            old = folder / "old"
            old.mkdir(exist_ok=True)
            os.replace(dest, old / f"{talent_id}-{now:%Y%m%dT%H%M%SZ}{RESUME_SUFFIX}")
            report.replaced += 1
        else:
            report.added += 1
        _copy_atomic(source, dest)
    return report


def stored_resume(data_dir: Path, talent_id: str, known_ids: Iterable[str]) -> Path | None:
    """The stored résumé of a contractor in the list, if any. The path is built only from an
    ID found in the list, never from the caller's text."""
    talent_id = next((k for k in known_ids if k == talent_id), "")
    if not talent_id:
        return None
    path = resumes_dir(data_dir) / f"{talent_id}{RESUME_SUFFIX}"
    return path if path.is_file() else None
