"""0.4 recommendations in rounds, rescans that send only new or changed contractors, and the
application statuses (user decisions 2026-11-09). Unit tests of llm/rounds.py, then the whole
flow through the app service with a fake Gemini that scores each contractor. No network."""

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import staged_answer
from jobmatch.app.service import AppError, AppService
from jobmatch.config import LlmShortlistSettings, Settings
from jobmatch.llm.prompt import SCREEN_SYSTEM
from jobmatch.llm.provider import LlmRequest, LlmResponse, TokenUsage
from jobmatch.llm.rounds import arrange, seats, shortlist
from jobmatch.schemas import (
    ApplicationStatus,
    ContractorProfile,
    Decision,
    Job,
    JobAvailability,
    LlmMatch,
    LlmVerdict,
    ReasonCode,
    RoundEvent,
)
from jobmatch.sources.contractor_upsert import upsert_contractors
from jobmatch.storage.csv_store import (
    CsvAppendRepository,
    CsvContractorProfileRepository,
    CsvJobRepository,
    CsvStore,
)
from test_app_ui import run_ui

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
RULES = LlmShortlistSettings(max_fits=3, partials_when_no_fit=1)


def row(talent: str, score: int, verdict: LlmVerdict | None = None, key: str = "k1") -> LlmMatch:
    verdict = verdict or (LlmVerdict.FIT if score >= 70 else LlmVerdict.PARTIAL)
    return LlmMatch(
        match_id=f"{key}-{talent}", job_id="paste:1", talent_id=talent, rank=1, verdict=verdict,
        fit_score=score, model="fake", prompt_version="v", judge_key=key, created_at=T0,
    )  # fmt: skip


def step(
    pool: dict[str, LlmMatch],
    states: dict,
    events: list[RoundEvent],
    *,
    newcomers: bool = False,
) -> list[RoundEvent]:
    new = arrange("paste:1", pool, states, events, RULES, newcomers=newcomers, now=T0)
    events.extend(new)
    return new


def shown(pool: dict[str, LlmMatch], states: dict, events: list[RoundEvent]) -> list[str]:
    return [m.talent_id for m in shortlist(seats(events), pool, states).shown]


# ---- llm/rounds.py ----


def test_first_round_takes_the_best_three_fits() -> None:
    pool = {t: row(t, s) for t, s in [("A", 95), ("B", 90), ("C", 85), ("D", 80), ("E", 60)]}
    events: list[RoundEvent] = []
    step(pool, {}, events)
    assert shown(pool, {}, events) == ["A", "B", "C"]
    assert {e.round for e in events} == {1}


def test_only_partials_means_the_single_best_partial() -> None:
    pool = {"A": row("A", 55), "B": row("B", 65)}
    events: list[RoundEvent] = []
    step(pool, {}, events)
    assert shown(pool, {}, events) == ["B"]


def test_a_better_newcomer_bumps_the_lowest_awaiting_not_the_approved() -> None:
    # 1 approved + 2 awaiting; a newcomer beats one of the 2 (the user's example).
    pool = {t: row(t, s) for t, s in [("A", 75), ("B", 90), ("C", 80)]}
    events: list[RoundEvent] = []
    step(pool, {}, events)
    states = {"A": "approved"}
    pool["N"] = row("N", 85, key="k2")
    step(pool, states, events)
    assert shown(pool, states, events) == ["B", "N", "A"]  # best first, approved included
    assert "C" not in seats(events)  # back in the reserve


def test_decided_slots_stay_and_a_full_round_waits_for_newcomers() -> None:
    pool = {t: row(t, s) for t, s in [("A", 95), ("B", 90), ("C", 85), ("D", 80)]}
    events: list[RoundEvent] = []
    step(pool, {}, events)
    states = {"A": "approved", "B": "rejected", "C": "rejected"}
    # Everyone decided, newcomers still to scan: nothing opens yet (user decision C).
    assert step(pool, states, events, newcomers=True) == []
    # Scanned: the newcomer and the reserve compete for round 2.
    pool["N"] = row("N", 88, key="k2")
    new = step(pool, states, events)
    assert {e.round for e in new} == {2}
    assert shown(pool, states, events) == ["A", "N", "D"]
    hidden = shortlist(seats(events), pool, states).rejected
    assert [m.talent_id for m in hidden] == ["B", "C"]  # behind "Show rejected"


def test_a_reset_rejection_comes_back_and_a_removed_one_never_shows() -> None:
    pool = {t: row(t, s) for t, s in [("A", 95), ("B", 90)]}
    events: list[RoundEvent] = []
    step(pool, {}, events)
    assert shown(pool, {"A": "rejected"}, events) == ["B"]
    assert shown(pool, {}, events) == ["A", "B"]  # reset: awaiting again
    states = {"A": "removed"}
    picture = shortlist(seats(events), pool, states)
    assert [m.talent_id for m in picture.shown] == ["B"] and picture.rejected == []


def test_decisions_made_before_rounds_keep_their_place() -> None:
    # 0.3 data: approved contractors with no round yet are seated in round 1.
    pool = {t: row(t, s) for t, s in [("A", 95), ("B", 90), ("C", 85), ("D", 80)]}
    events: list[RoundEvent] = []
    step(pool, {"B": "approved"}, events)
    assert shown(pool, {"B": "approved"}, events) == ["A", "B", "C"]


# ---- the whole flow, with a scoring fake ----


class ScoringGemini:
    """Rates each contractor in the request by a fixed score per alias (70+ fit, 40-69
    partial, else no). Records the aliases each screening call received."""

    model = "fake"

    def __init__(self, scores: dict[str, int]) -> None:
        self.scores = scores
        self.calls = 0
        self.screened: list[list[str]] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        aliases = re.findall(r"^\[(C\d+)\]", request.user, re.M)
        verdicts, top = [], []
        for alias in aliases:
            score = self.scores.get(alias, 10)
            verdict = "fit" if score >= 70 else "partial" if score >= 40 else "no"
            verdicts.append({"alias": alias, "verdict": verdict, "reason": "Dashboards."})
            if verdict != "no":
                top.append({
                    "alias": alias, "fit_score": score, "requirements_met": [],
                    "requirements_missing": [], "red_flags": [], "evidence": [],
                })  # fmt: skip
        if request.system == SCREEN_SYSTEM:
            self.screened.append(aliases)
        combined = {"verdicts": verdicts, "top": top, "no_fit": not top}
        usage = TokenUsage(input_tokens=1000, output_tokens=100, total_tokens=1100)
        return LlmResponse(
            text=staged_answer(combined, request), usage=usage, model="fake", http_status=200,
            attempts=1,
        )  # fmt: skip


def contractors(tmp_path: Path, n: int, headlines: dict[int, str] | None = None) -> Path:
    """A picked contractor file with TAL-1..TAL-n (aliases C01..: sorted by talent ID)."""
    path = tmp_path / f"picked-{uuid.uuid4().hex[:6]}" / "contractors.csv"
    profiles = [
        ContractorProfile(
            talent_id=f"TAL-{i}",
            talent_name=f"Person {i}",
            rate_expectation="90",
            standardized_headline=(headlines or {}).get(i, "Data Analyst"),
            executive_summary="Builds dashboards in Power BI for public agencies.",
        )  # fmt: skip
        for i in range(1, n + 1)
    ]
    upsert_contractors(profiles, CsvContractorProfileRepository(path, lock_timeout=5))
    return path


@pytest.fixture
def flow(
    app_settings: Settings, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> tuple[AppService, ScoringGemini]:
    # C01..C05 = TAL-1..TAL-5; C06 = TAL-6, added later.
    fake = ScoringGemini({"C01": 95, "C02": 90, "C03": 85, "C04": 80, "C05": 60, "C06": 99})
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ={}
    )
    CsvJobRepository(store).add_new([
        make_job(job_id="paste:1", source_id="1", title_norm="Analyste de données (contrat)",
                 responsibilities=["Tableaux de bord Power BI"], language="fr"),
    ])  # fmt: skip
    return service, fake


def run(service: AppService, path: Path | None = None) -> None:
    if path is not None:
        service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: True)


def names(service: AppService) -> list[str]:
    [job] = service.results()
    return [v.match.talent_id for v in job.matches]


def match_of(service: AppService, talent: str) -> str:
    [job] = service.results()
    return next(v.match.match_id for v in job.matches + job.rejected if v.match.talent_id == talent)


def test_rescans_rounds_and_applications(flow: tuple[AppService, ScoringGemini], tmp_path: Path):
    service, fake = flow
    run(service, contractors(tmp_path, 5))
    assert names(service) == ["TAL-1", "TAL-2", "TAL-3"]
    assert fake.screened == [["C01", "C02", "C03", "C04", "C05"]]

    # Nothing new: a rerun sends nothing.
    calls = fake.calls
    run(service)
    assert fake.calls == calls

    # A new contractor, better than everyone: only they are sent, and they take the slot of
    # the lowest one awaiting review; the approved one keeps theirs.
    service.decide(match_of(service, "TAL-1"), Decision.APPROVED)
    run(service, contractors(tmp_path, 6))
    assert fake.screened[-1] == ["C06"]
    assert names(service) == ["TAL-6", "TAL-1", "TAL-2"]

    # A reviewer rejection is hidden behind "Show rejected"; Reset brings it back.
    service.decide(match_of(service, "TAL-2"), Decision.REJECTED, ReasonCode.SENIORITY)
    [job] = service.results()
    assert [v.match.talent_id for v in job.rejected] == ["TAL-2"]
    assert names(service) == ["TAL-6", "TAL-1"]
    service.clear_decision(match_of(service, "TAL-2"))
    assert names(service) == ["TAL-6", "TAL-1", "TAL-2"]

    # A changed profile is sent again, unless the contractor has a decision for the job.
    service.decide(match_of(service, "TAL-2"), Decision.REJECTED, ReasonCode.SENIORITY)
    run(service, contractors(tmp_path, 6, headlines={2: "Analyste BI", 4: "Analyste BI"}))
    assert fake.screened[-1] == ["C04"]

    # Everyone in the round decided: the next round opens from the reserve.
    service.decide(match_of(service, "TAL-6"), Decision.APPROVED)
    assert names(service) == ["TAL-6", "TAL-1", "TAL-3", "TAL-4"]

    # Jobs screen: the approved candidates and their application status.
    [overview] = service.jobs_overview()
    table = {c.talent_id: c.status for c in overview.candidates}
    assert table == {"TAL-6": ApplicationStatus.NOT_REFERRED, "TAL-1": "not_referred"}
    with pytest.raises(AppError, match="approved"):
        service.set_application("paste:1", "TAL-3", ApplicationStatus.OUTREACHED)

    # HR rejected or ghosted: out of consideration everywhere, even the rejected list...
    service.set_application("paste:1", "TAL-1", ApplicationStatus.OUTREACHED)
    service.set_application("paste:1", "TAL-1", ApplicationStatus.REJECTED)
    [job] = service.results()
    assert "TAL-1" not in [v.match.talent_id for v in job.matches + job.rejected]
    [overview] = service.jobs_overview()
    assert [(c.talent_id, c.removed) for c in overview.candidates][-1] == ("TAL-1", True)
    with pytest.raises(AppError, match="Undo"):
        service.set_application("paste:1", "TAL-1", ApplicationStatus.HIRED)
    # ...until Undo on the Jobs screen: back to the status before.
    assert service.undo_application_rejection("paste:1", "TAL-1") is ApplicationStatus.OUTREACHED
    assert "TAL-1" in names(service)

    # Hired: the job is completed (the screen, below, uses the same call).
    service.set_application("paste:1", "TAL-6", ApplicationStatus.HIRED)
    [overview] = service.jobs_overview()
    assert overview.state == "inactive"
    assert overview.job.availability is JobAvailability.COMPLETED
    assert service.results() == []


def test_screens_show_rejected_and_save_a_status(
    flow: tuple[AppService, ScoringGemini], store: CsvStore, tmp_path: Path
) -> None:
    service, _ = flow
    run(service, contractors(tmp_path, 3))
    service.decide(match_of(service, "TAL-1"), Decision.APPROVED)
    service.decide(match_of(service, "TAL-2"), Decision.REJECTED, ReasonCode.SENIORITY)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/results")
        await user.should_see("Show rejected (1)")
        # Reset a rejection: back among the recommendations at once, no reload.
        user.find(marker="reset:TAL-2").click()
        await user.should_not_see("Show rejected")
        await user.should_see(marker="reject:TAL-2")
        # Reject: moves under "Show rejected" at once.
        user.find(marker="reject:TAL-3").click()
        await user.should_see(marker="reject-reason")  # the dialog opens asynchronously
        [reason] = user.find(marker="reject-reason").elements
        reason.value = ReasonCode.SENIORITY.value
        user.find(marker="reject-confirm").click()
        await user.should_see("Show rejected (1)")
        # Rejected again for the rest of this test (as before the reset above).
        user.find(marker="reject:TAL-2").click()
        await user.should_see(marker="reject-reason")
        [reason] = user.find(marker="reject-reason").elements
        reason.value = ReasonCode.SENIORITY.value
        user.find(marker="reject-confirm").click()
        await user.should_see("Show rejected (2)")
        await user.open("/jobs")
        await user.should_see("Approved candidates (1)")
        await user.should_see("Person 1")
        [select] = user.find(marker="status:paste:1:TAL-1").elements
        select.value = ApplicationStatus.IN_PROGRESS.value
        user.find(marker="save-status:paste:1:TAL-1").click()
        await user.should_see("Saved.")
        [select] = user.find(marker="status:paste:1:TAL-1").elements
        select.value = ApplicationStatus.REJECTED.value
        user.find(marker="save-status:paste:1:TAL-1").click()
        await user.should_see(marker="undo:paste:1:TAL-1")  # greyed, with Undo
        user.find(marker="undo:paste:1:TAL-1").click()
        await user.should_see(marker="status:paste:1:TAL-1")

    run_ui(service, scenario)
    statuses = [a.status for a in CsvAppendRepository(store.applications).list_all()]
    assert statuses == ["in_progress", "rejected", "in_progress"]
