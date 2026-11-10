"""Export and import of all the app's data (Settings screen; user decisions 2026-11-10), to
back up, or to move the app to another computer or person.

Export: one password-protected zip (AES-256, pyzipper) holding every file of the data folder
under "data/", plus "manifest.json" (app version, data format version, date, and the size
and SHA-256 of every file). Left out: the app's timestamped backup folders ("backups" at any
level), lock files, and the .env file (the Gemini key and reviewer name never leave this
machine: CLAUDE.md rule 7). The export holds the run lock, so a run never half-writes it.

Import replaces everything: the backup is checked first (password, every checksum, no file
outside the data folder, a data format this app reads, every CSV valid), then the current
data is moved, never deleted, into data/backups/before-import-<time>/ and the backup is
unpacked in its place. Moving that folder back undoes an import.

No network. Contractor data is personal: nothing here is logged except counts.
"""

import csv
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath

import pyzipper
from filelock import FileLock, Timeout
from pydantic import BaseModel

from jobmatch.schemas import SCHEMA_VERSION
from jobmatch.storage.csv_store import check_data_folder

MANIFEST = "manifest.json"
DATA_PREFIX = "data/"
BACKUP_DIR_NAME = "backups"  # skipped at any level (data/backups, data/contractors/backups)
PIPELINE_LOCK = ".pipeline.lock"  # pipeline.PIPELINE_LOCK_FILE (no import: avoids a cycle)
MIN_PASSWORD = 10
MAX_TOTAL_BYTES = 5 * 1024**3  # a sanity cap on what an import may unpack
FORMAT = 1  # this file's own layout


class BackupError(Exception):
    """A problem with a backup, in plain words (never holds contractor data)."""


class ManifestFile(BaseModel):
    path: str  # relative to the data folder, "/"-separated
    size: int
    sha256: str


class Manifest(BaseModel):
    format: int = FORMAT
    app_version: str
    schema_version: int
    created_at: datetime
    jobs: int
    contractors: int
    files: list[ManifestFile]


class BackupInfo(BaseModel):
    """What a checked backup holds (shown before an import)."""

    created_at: datetime
    app_version: str
    jobs: int
    contractors: int
    files: int
    size: int


def app_version() -> str:
    try:
        return version("jobmatch")
    except PackageNotFoundError:
        return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_files(data_dir: Path) -> Iterator[Path]:
    """Every file that belongs in an export, in a stable order."""
    for root, dirs, files in os.walk(data_dir):
        dirs[:] = sorted(d for d in dirs if d != BACKUP_DIR_NAME)
        for name in sorted(files):
            if name.endswith(".lock") or name == ".env":
                continue
            yield Path(root) / name


def _count_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        return max(sum(1 for row in csv.reader(f) if any(c.strip() for c in row)) - 1, 0)


def _counts(data_dir: Path, contractors_rel: Path) -> tuple[int, int]:
    return _count_rows(data_dir / "jobs.csv"), _count_rows(data_dir / contractors_rel)


def current_counts(data_dir: Path, contractors_rel: Path) -> tuple[int, int]:
    """(jobs, contractors) in the data folder now."""
    return _counts(data_dir, contractors_rel)


def _password(text: str) -> bytes:
    return text.encode("utf-8")


def export_data(
    data_dir: Path,
    dest: Path,
    password: str,
    *,
    contractors_rel: Path,
    lock_timeout: float,
    now: datetime | None = None,
) -> BackupInfo:
    """Write the encrypted backup to `dest` (written to a temporary name first)."""
    if len(password) < MIN_PASSWORD:
        raise BackupError(f"Use a password of at least {MIN_PASSWORD} characters.")
    now = now or datetime.now(UTC)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(data_dir / PIPELINE_LOCK), timeout=lock_timeout):
            files = [p for p in data_files(data_dir) if p.resolve() != dest.resolve()]
            entries = [
                ManifestFile(
                    path=p.relative_to(data_dir).as_posix(),
                    size=p.stat().st_size,
                    sha256=_sha256(p),
                )
                for p in files
            ]
            jobs, contractors = _counts(data_dir, contractors_rel)
            manifest = Manifest(
                app_version=app_version(), schema_version=SCHEMA_VERSION, created_at=now,
                jobs=jobs, contractors=contractors, files=entries,
            )  # fmt: skip
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".export-", suffix=".zip")
            os.close(fd)
            try:
                with pyzipper.AESZipFile(
                    tmp, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
                ) as zf:
                    zf.setpassword(_password(password))
                    zf.setencryption(pyzipper.WZ_AES, nbits=256)
                    zf.writestr(MANIFEST, manifest.model_dump_json(indent=2))
                    for path, entry in zip(files, entries, strict=True):
                        zf.write(path, DATA_PREFIX + entry.path)
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
    except Timeout as exc:
        raise BackupError("A run is in progress. Export when it ends.") from exc
    return BackupInfo(
        created_at=now, app_version=manifest.app_version, jobs=jobs, contractors=contractors,
        files=len(entries), size=dest.stat().st_size,
    )  # fmt: skip


def _safe_rel(name: str) -> PurePosixPath:
    """A data file's path inside the zip, refused if it could land outside the folder."""
    if not name.startswith(DATA_PREFIX) or "\\" in name:
        raise BackupError(f"The backup holds an unexpected file ({name!r}).")
    rel = PurePosixPath(name[len(DATA_PREFIX) :])
    if rel.is_absolute() or not rel.parts or any(p in ("..", "") for p in rel.parts):
        raise BackupError(f"The backup holds a file outside the data folder ({name!r}).")
    if BACKUP_DIR_NAME in rel.parts or rel.name.endswith(".lock"):
        raise BackupError(f"The backup holds a file it should not ({name!r}).")
    return rel


def _open(path: Path, password: str) -> tuple[pyzipper.AESZipFile, Manifest]:
    try:
        zf = pyzipper.AESZipFile(path)
    except (pyzipper.BadZipFile, OSError) as exc:
        raise BackupError(
            f"{path.name} is not a backup made by this app (not a zip file)."
        ) from exc
    zf.setpassword(_password(password))
    try:
        manifest = Manifest.model_validate_json(zf.read(MANIFEST))
    except KeyError as exc:
        zf.close()
        raise BackupError(f"{path.name} is not a backup made by this app (no manifest).") from exc
    except RuntimeError as exc:  # pyzipper: "Bad password for file"
        zf.close()
        raise BackupError("Wrong password for this backup.") from exc
    except ValueError as exc:
        zf.close()
        raise BackupError(f"{path.name}: its manifest is damaged.") from exc
    return zf, manifest


def check_backup(
    path: Path, password: str, *, work_dir: Path, contractors_rel: Path, lock_timeout: float
) -> tuple[BackupInfo, Path]:
    """Check everything and unpack into a new folder under `work_dir`. Returns what the
    backup holds and that folder (the caller moves it into place, or deletes it)."""
    zf, manifest = _open(path, password)
    with zf:
        if manifest.format != FORMAT:
            raise BackupError("This backup was made by a different version of the app.")
        if manifest.schema_version > SCHEMA_VERSION:
            raise BackupError(
                "This backup was made by a newer version of the app. Update this app first."
            )
        if manifest.schema_version < SCHEMA_VERSION:
            raise BackupError(
                f"This backup uses an older data format ({manifest.schema_version}); this app "
                f"reads format {SCHEMA_VERSION}."
            )
        listed = {f.path: f for f in manifest.files}
        names = [n for n in zf.namelist() if n != MANIFEST and not n.endswith("/")]
        rels = {str(_safe_rel(n)): n for n in names}
        if set(rels) != set(listed):
            raise BackupError("The backup's files do not match its manifest: it may be damaged.")
        if sum(f.size for f in manifest.files) > MAX_TOTAL_BYTES:
            raise BackupError("The backup is larger than this app accepts.")
        work_dir.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(dir=work_dir, prefix=".importing-"))
        try:
            for rel, name in rels.items():
                entry = listed[rel]
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(name) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                except RuntimeError as exc:
                    raise BackupError("Wrong password for this backup.") from exc
                if dest.stat().st_size != entry.size or _sha256(dest) != entry.sha256:
                    raise BackupError(f"A file in the backup is damaged ({rel}).")
            _validate(target, contractors_rel, lock_timeout)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
    total = sum(f.size for f in manifest.files)
    info = BackupInfo(
        created_at=manifest.created_at, app_version=manifest.app_version, jobs=manifest.jobs,
        contractors=manifest.contractors, files=len(manifest.files), size=total,
    )  # fmt: skip
    return info, target


def _validate(folder: Path, contractors_rel: Path, lock_timeout: float) -> None:
    """Every CSV the app reads, checked against its schema (as validate-data does)."""
    problems = check_data_folder(folder, contractors_rel, lock_timeout=lock_timeout)
    if problems:
        shown = "; ".join(problems[:3])
        more = f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""
        raise BackupError(f"The backup's data does not pass the checks: {shown}{more}")


def replace_data(data_dir: Path, unpacked: Path, *, lock_timeout: float, now: datetime) -> Path:
    """Move the current data into data/backups/before-import-<time>/, then the unpacked
    backup into the data folder. Returns where the old data went. Nothing is deleted."""
    keep = data_dir / BACKUP_DIR_NAME / f"before-import-{now:%Y%m%dT%H%M%SZ}"
    try:
        with FileLock(str(data_dir / PIPELINE_LOCK), timeout=lock_timeout):
            keep.mkdir(parents=True, exist_ok=False)
            for entry in sorted(data_dir.iterdir()):
                if entry.name == BACKUP_DIR_NAME or entry.name.endswith(".lock"):
                    continue
                shutil.move(str(entry), str(keep / entry.name))
            for entry in sorted(unpacked.iterdir()):
                shutil.move(str(entry), str(data_dir / entry.name))
            unpacked.rmdir()
    except Timeout as exc:
        raise BackupError("A run is in progress. Import when it ends.") from exc
    return keep


def backup_name(now: datetime) -> str:
    return f"do-good-talent-backup-{now:%Y-%m-%dT%H%MZ}.zip"
