"""Export and import of all data (user decisions 2026-11-10): one AES-encrypted zip with a
manifest; import checks everything, then replaces the data, keeping the old data in
data/backups/before-import-<time>/. The key and reviewer name are never exported. No network."""

import json
from pathlib import Path

import pytest
import pyzipper

from jobmatch.app.service import AppError, AppService
from jobmatch.config import Settings
from jobmatch.schemas import ApplicationStatus, Decision
from jobmatch.sources.resumes import resumes_dir
from test_resumes import pdf
from test_talent import MarkerGemini, match_of, talent  # noqa: F401 (fixture)

PASSWORD = "correct horse battery"


def tamper(src: Path, dest: Path, change) -> Path:  # noqa: ANN001
    """Rewrite a backup with `change(name, data) -> data | None` (None drops the entry)."""
    with pyzipper.AESZipFile(src) as zin:
        zin.setpassword(PASSWORD.encode())
        entries = [(n, zin.read(n)) for n in zin.namelist()]
    with pyzipper.AESZipFile(dest, "w", encryption=pyzipper.WZ_AES) as zout:
        zout.setpassword(PASSWORD.encode())
        for name, data in entries:
            for new_name, new_data in change(name, data):
                zout.writestr(new_name, new_data)
    return dest


def test_round_trip_replaces_and_keeps_the_old_data(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, _ = talent
    service.decide(match_of(service, "TAL-1"), Decision.APPROVED)
    service.set_application("paste:1", "TAL-1", ApplicationStatus.IN_PROGRESS)
    pdf(resumes_dir(service.data_dir) / "TAL-1.pdf", "Émilie")
    (service.data_dir / "backups" / "old.csv").parent.mkdir(parents=True, exist_ok=True)
    (service.data_dir / "backups" / "old.csv").write_text("x")

    with pytest.raises(AppError, match="at least 10"):
        service.export_data("short")
    info, path = service.export_data(PASSWORD)
    assert (info.jobs, info.contractors) == (1, 5)
    with pyzipper.AESZipFile(path) as zf:
        zf.setpassword(PASSWORD.encode())
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert "data/contractors/resumes/TAL-1.pdf" in names
    assert not any("backups/" in n or n.endswith(".lock") or ".env" in n for n in names)
    assert manifest["jobs"] == 1 and all(len(f["sha256"]) == 64 for f in manifest["files"])
    with pytest.raises(RuntimeError, match="password required"):  # really encrypted
        pyzipper.ZipFile(path).read("manifest.json")

    # Things change after the export; the import brings the exported state back.
    service.set_talent_availability("TAL-2", False, "Away")
    service.set_job_availability("paste:1", "unavailable")
    before = service.check_import(path, PASSWORD)
    assert (before.jobs, before.contractors) == (1, 5)
    kept = service.import_checked()
    assert kept.parent == service.data_dir / "backups" and kept.name.startswith("before-import-")
    assert {t.talent_id: t.available for t in service.talent_overview()}["TAL-2"]
    assert [r.job.job_id for r in service.results()] == ["paste:1"]  # open again
    assert service.resume_path("TAL-1") is not None
    assert (kept / "jobs.csv").exists()  # the replaced data, moved, not deleted
    assert (service.data_dir / "backups" / "old.csv").exists()  # backups left alone


def test_bad_backups_change_nothing(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, _ = talent
    _, path = service.export_data(PASSWORD)
    jobs_before = (service.data_dir / "jobs.csv").read_bytes()

    with pytest.raises(AppError, match="Wrong password"):
        service.check_import(path, "not the password")

    damaged = tamper(path, tmp_path / "damaged.zip", lambda n, d: [
        (n, d.replace(b"Power BI", b"Excel") if n == "data/jobs.csv" else d)
    ])  # fmt: skip
    with pytest.raises(AppError, match="damaged"):
        service.check_import(damaged, PASSWORD)

    escaping = tamper(path, tmp_path / "escape.zip", lambda n, d: [(n, d)] + (
        [("data/../../evil.txt", b"x")] if n == "manifest.json" else []
    ))  # fmt: skip
    with pytest.raises(AppError, match="outside the data folder"):
        service.check_import(escaping, PASSWORD)

    def newer(name: str, data: bytes) -> list[tuple[str, bytes]]:
        if name != "manifest.json":
            return [(name, data)]
        manifest = json.loads(data)
        manifest["schema_version"] += 1
        return [(name, json.dumps(manifest).encode())]

    with pytest.raises(AppError, match="newer version"):
        service.check_import(tamper(path, tmp_path / "newer.zip", newer), PASSWORD)

    not_zip = tmp_path / "notes.zip"
    not_zip.write_text("hello")
    with pytest.raises(AppError, match="not a zip"):
        service.check_import(not_zip, PASSWORD)

    with pytest.raises(AppError, match="Check the backup again"):
        service.import_checked()
    assert (service.data_dir / "jobs.csv").read_bytes() == jobs_before
    assert not list((service.data_dir / "backups").glob(".importing-*"))  # nothing left over


def test_move_to_a_new_machine(talent, app_settings: Settings, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, _ = talent
    _, path = service.export_data(PASSWORD)
    other_dir = tmp_path / "new-machine" / "data"
    other_settings = app_settings.model_copy(
        update={"paths": app_settings.paths.model_copy(update={
            "data_dir": other_dir, "contractors_csv": other_dir / "contractors" / "contractors.csv",
        })}
    )  # fmt: skip
    fresh = AppService(
        other_settings, tmp_path / "new.env", provider_factory=MarkerGemini, environ={}
    )
    assert fresh.data_counts() == (0, 0)
    fresh.check_import(path, PASSWORD)
    fresh.import_checked()
    assert fresh.data_counts() == (1, 5)
    assert not fresh.has_key()  # the key never travels: entered again in Settings


def test_settings_card(talent) -> None:  # noqa: ANN001, F811
    from test_app_ui import run_ui

    service, _ = talent

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/settings")
        await user.should_see("Back up or move the app")
        user.find(marker="export-data").click()
        await user.should_see(marker="export-password")
        user.find(marker="export-password").type("short")
        user.find(marker="export-password-again").type("short")
        user.find(marker="export-confirm").click()
        await user.should_see("Use a password of at least 10 characters.")

    run_ui(service, scenario)
