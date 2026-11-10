"""Rematch only changed contractors (user decisions 2026-11-10): an import marks who changed
by comparing the stored rows with the new ones; edits Gemini never sees (name, phone, email)
are not a change; a changed job, model or prompt resends nobody. Fake Gemini, no network."""

from pathlib import Path

from jobmatch.app.service import AppService
from jobmatch.schemas import ContractorProfile
from test_talent import MarkerGemini, picked, profile, talent  # noqa: F401 (fixture)


def changes(service: AppService) -> list:
    """Change rows after the fixture's first import (which added everyone)."""
    return [c for c in service._loader().contractor_changes.list_all() if c.change != "added"
            or c.talent_id == "TAL-6"]  # fmt: skip


def import_and_match(service: AppService, tmp_path: Path, rows: list[ContractorProfile]) -> None:
    path = picked(tmp_path, rows)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: True)


def test_edits_gemini_never_sees_resend_nobody(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, fake = talent
    calls = fake.calls
    renamed = profile(1).model_copy(update={"talent_name": "Person One-Renamed"})
    import_and_match(service, tmp_path, [renamed])
    assert fake.calls == calls
    assert changes(service) == []


def test_only_the_changed_contractor_is_sent(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, fake = talent
    edited = profile(3).model_copy(
        update={"executive_summary": "Tableaux de bord Power BI et SQL. Marker85."}
    )
    import_and_match(service, tmp_path, [edited, profile(1), profile(2)])
    assert fake.screened[-1] == [85]  # TAL-3 only; TAL-1 and TAL-2 are unchanged
    [change] = changes(service)
    assert (change.talent_id, change.change, change.columns) == (
        "TAL-3", "updated", ["executive_summary"],
    )  # fmt: skip
    # Matched since: the next Find matches sends nobody.
    calls = fake.calls
    service.run(echo=lambda s: None, confirm=lambda c: True)
    assert fake.calls == calls


def test_new_contractor_is_sent_alone(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, fake = talent
    import_and_match(service, tmp_path, [profile(6)])
    assert fake.screened[-1] == [75]
    assert [(c.talent_id, c.change) for c in changes(service)] == [("TAL-6", "added")]


def test_model_change_resends_nobody(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, fake = talent
    llm = service.settings.llm
    assert llm is not None
    newer = service.settings.model_copy(
        update={"llm": llm.model_copy(update={"model": "fake-newer"})}
    )
    other = AppService(newer, tmp_path / "app.env", provider_factory=lambda: fake, environ={})
    calls = fake.calls
    other.run(echo=lambda s: None, confirm=lambda c: True)
    assert fake.calls == calls
