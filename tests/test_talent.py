"""The Talent screen (user decisions 2026-11-09): every contractor, the reviewer's "Available
for matching" toggle (overrides the profile either way, never set automatically), and the
jobs each was approved for. A fake Gemini that scores each profile by a marker in its text
(aliases shift when someone is left out). No network."""

import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from nicegui import ui

from conftest import staged_answer
from jobmatch.app.service import AppError, AppService
from jobmatch.config import Settings
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.prompt import SCREEN_SYSTEM
from jobmatch.llm.provider import LlmRequest, LlmResponse, TokenUsage
from jobmatch.schemas import ApplicationStatus, ContractorProfile, Decision, Job
from jobmatch.sources.contractor_upsert import upsert_contractors
from jobmatch.storage.csv_store import CsvContractorProfileRepository, CsvJobRepository, CsvStore
from test_app_ui import run_ui

SCORES = {1: 95, 2: 90, 3: 85, 4: 80, 5: 99, 6: 75}  # TAL-n -> score; TAL-5: not contract


def profile(n: int, contract: bool = True) -> ContractorProfile:
    return ContractorProfile(
        talent_id=f"TAL-{n}",
        talent_name=f"Person {n}",
        rate_expectation="90",
        standardized_headline="Analyste de données" if n % 2 else "Data Analyst",
        executive_summary=f"Builds dashboards in Power BI. Marker{SCORES[n]}.",
        contract=contract,
    )


class MarkerGemini:
    """Rates each profile in the request by its "MarkerNN" (70+ fit, 40-69 partial)."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.screened: list[list[str]] = []  # the markers each screening call received

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        blocks = re.split(r"^\[(C\d+)\]", request.user, flags=re.M)[1:]
        cards = dict(zip(blocks[::2], blocks[1::2], strict=True))
        verdicts, top, markers = [], [], []
        for alias, text in cards.items():
            found = re.search(r"Marker(\d+)", text)
            score = int(found.group(1)) if found else 10
            markers.append(score)
            verdict = "fit" if score >= 70 else "partial" if score >= 40 else "no"
            verdicts.append({"alias": alias, "verdict": verdict, "reason": "Dashboards."})
            if verdict != "no":
                top.append({
                    "alias": alias, "fit_score": score, "requirements_met": [],
                    "requirements_missing": [], "red_flags": [], "evidence": [],
                })  # fmt: skip
        if request.system == SCREEN_SYSTEM:
            self.screened.append(markers)
        combined = {"verdicts": verdicts, "top": top, "no_fit": not top}
        usage = TokenUsage(input_tokens=1000, output_tokens=100, total_tokens=1100)
        return LlmResponse(
            text=staged_answer(combined, request), usage=usage, model="fake", http_status=200,
            attempts=1,
        )  # fmt: skip


def picked(tmp_path: Path, profiles: list[ContractorProfile]) -> Path:
    path = tmp_path / f"picked-{uuid.uuid4().hex[:6]}" / "contractors.csv"
    upsert_contractors(profiles, CsvContractorProfileRepository(path, lock_timeout=5))
    return path


@pytest.fixture
def talent(
    app_settings: Settings, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> tuple[AppService, MarkerGemini]:
    fake = MarkerGemini()
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ={}
    )
    CsvJobRepository(store).add_new([
        make_job(job_id="paste:1", source_id="1", title_norm="Analyste de données (contrat)",
                 company_raw="Ville de Québec", location_raw="Québec, QC",
                 responsibilities=["Tableaux de bord Power BI"], language="fr"),
    ])  # fmt: skip
    path = picked(tmp_path, [profile(n) for n in (1, 2, 3, 4)] + [profile(5, contract=False)])
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: True)
    return service, fake


def shown(service: AppService) -> list[str]:
    [job] = service.results()
    return [v.match.talent_id for v in job.matches]


def match_of(service: AppService, talent_id: str) -> str:
    [job] = service.results()
    return next(v.match.match_id for v in job.matches if v.match.talent_id == talent_id)


def test_roster_follows_the_toggle(app_settings: Settings) -> None:
    assert app_settings.llm is not None
    payload = app_settings.llm.payload
    profiles = [profile(1), profile(5, contract=False)]
    assert list(build_roster(profiles, payload).aliases.values()) == ["TAL-1"]
    # The reviewer checked with them: available although the profile says no, and the
    # other way round.
    roster = build_roster(profiles, payload, {"TAL-5": True, "TAL-1": False})
    assert list(roster.aliases.values()) == ["TAL-5"]
    assert [e.reason for e in roster.excluded] == ["marked unavailable"]


def test_talent_list_and_toggle(talent: tuple[AppService, MarkerGemini], tmp_path: Path):
    service, fake = talent
    assert fake.screened == [[95, 90, 85, 80]]  # TAL-5's profile says not open: not sent
    assert shown(service) == ["TAL-1", "TAL-2", "TAL-3"]

    overview = service.talent_overview()
    # Available A–Z, then not available A–Z.
    assert [(t.talent_id, t.available) for t in overview] == [
        ("TAL-1", True), ("TAL-2", True), ("TAL-3", True), ("TAL-4", True), ("TAL-5", False),
    ]  # fmt: skip
    assert overview[-1].profile_open is False and overview[-1].toggled is None

    # Approved and in progress with HR: listed under the contractor.
    service.decide(match_of(service, "TAL-1"), Decision.APPROVED)
    service.set_application("paste:1", "TAL-1", ApplicationStatus.IN_PROGRESS)
    [first] = [t for t in service.talent_overview() if t.talent_id == "TAL-1"]
    [row] = first.jobs
    assert (row.title, row.company, row.location) == (
        "Analyste de données (contrat)", "Ville de Québec", "Québec, QC",
    )  # fmt: skip
    assert row.status is ApplicationStatus.IN_PROGRESS and row.job_open

    # Not available: awaiting recommendations make room (TAL-4 takes TAL-2's slot); the
    # approved one stays, tagged.
    service.set_talent_availability("TAL-2", False, "  Sur un mandat   jusqu'en mars ")
    service.set_talent_availability("TAL-1", False)
    assert shown(service) == ["TAL-1", "TAL-3", "TAL-4"]
    [job] = service.results()
    assert [v.unavailable for v in job.matches] == [True, False, False]
    [second] = [t for t in service.talent_overview() if t.talent_id == "TAL-2"]
    assert second.toggled is not None and second.toggled.note == "Sur un mandat jusqu'en mars"
    assert [t.talent_id for t in service.talent_overview()][-3:] == ["TAL-1", "TAL-2", "TAL-5"]

    # Available although the profile says no: sent on the next run (only them).
    calls = fake.calls
    service.run(echo=lambda s: None, confirm=lambda c: True)
    assert fake.calls == calls  # nothing new to send yet
    service.set_talent_availability("TAL-5", True, "Confirmed by phone")
    service.run(echo=lambda s: None, confirm=lambda c: True)
    assert fake.screened[-1] == [99]

    # An import with a changed profile (the contractor filled the intake form again): their
    # own answer takes precedence over the earlier toggle. Unchanged rows keep the toggle.
    changed = profile(5, contract=False).model_copy(update={"location": "Montréal, QC"})
    path = picked(tmp_path, [changed, profile(2)])
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: True)
    by_id = {t.talent_id: t for t in service.talent_overview()}
    assert not by_id["TAL-5"].available  # reset from the profile
    assert by_id["TAL-5"].toggled is not None
    assert by_id["TAL-5"].toggled.reviewer == "contractor import"
    assert not by_id["TAL-2"].available  # unchanged: the reviewer's toggle stays
    # The reviewer can toggle again after the import. A hire makes them not available
    # (user decisions 2026-11-10); undoing the hire restores what it was before.
    service.set_talent_availability("TAL-1", True)
    service.set_application("paste:1", "TAL-1", ApplicationStatus.HIRED)
    by_id = {t.talent_id: t for t in service.talent_overview()}
    assert not by_id["TAL-1"].available
    assert by_id["TAL-1"].toggled is not None and by_id["TAL-1"].toggled.reviewer == "hire"
    assert by_id["TAL-1"].jobs[0].status is ApplicationStatus.HIRED
    assert by_id["TAL-1"].jobs[0].job_open is False
    service.set_application("paste:1", "TAL-1", ApplicationStatus.IN_PROGRESS)
    assert {t.talent_id: t.available for t in service.talent_overview()}["TAL-1"]
    service.set_application("paste:1", "TAL-1", ApplicationStatus.HIRED)

    # The hired job is off Results and never scanned again, even for a new contractor.
    assert service.results() == []
    calls = fake.calls
    path = picked(tmp_path, [profile(6)])
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: True)
    assert fake.calls == calls and service.results() == []

    with pytest.raises(AppError, match="not in the contractor list"):
        service.set_talent_availability("TAL-99", False)


def test_talent_screen(talent: tuple[AppService, MarkerGemini]) -> None:
    service, _ = talent

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/talent")
        await user.should_see("5 contractor(s) · 4 available for matching · 1 not available")
        await user.should_see("Profile says: not open to contract work")
        user.find(marker="talent-search").type("person 3")
        await user.should_not_see("Person 1")
        await user.should_see("Person 3")
        user.find(marker="talent-search").clear()
        await user.should_see("Person 1")
        user.find(marker="available:TAL-2").click()  # the switch: on -> off
        user.find(marker="note:TAL-2").type("Sur un mandat")
        user.find(marker="save-available:TAL-2").click()
        await user.should_see("Sur un mandat", marker="talent-note")
        # Opened again: the not available ones at the bottom, A–Z (Person 2, then 5).
        await user.open("/talent")
        await user.should_see("3 available for matching · 2 not available")
        names = sorted(
            (e for e in user.find(kind=ui.label).elements if e.text.startswith("Person ")),
            key=lambda e: e.id,
        )
        assert [e.text for e in names][-2:] == ["Person 2", "Person 5"]

    run_ui(service, scenario)
    assert not {t.talent_id: t.available for t in service.talent_overview()}["TAL-2"]
