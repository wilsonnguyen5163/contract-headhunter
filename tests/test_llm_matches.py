"""Cached Gemini judgments -> llm_matches rows (shortlist rule), job status, current shortlist
and export. No network, no API."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobmatch.config import LlmShortlistSettings
from jobmatch.llm.export import build_export_rows, write_llm_export
from jobmatch.llm.matches import judged_job
from jobmatch.llm.prompt import (
    ContractorVerdict,
    Evidence,
    JudgeOutput,
    RedFlag,
    TopMatch,
)
from jobmatch.llm.trial import Judged, TrialResult
from jobmatch.schemas import (
    ContractorProfile,
    Job,
    JobStatus,
    LlmCallStatus,
    LlmUsage,
    LlmVerdict,
)
from jobmatch.storage.csv_store import CsvLlmMatchRepository, CsvStore

AT = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
RULES = LlmShortlistSettings(max_fits=3, partials_when_no_fit=1)  # as in settings.yaml


def result(output: JudgeOutput | None, *, key: str = "k1", unverified: dict | None = None,
           job_id: str = "paste:1") -> TrialResult:  # fmt: skip
    usage = LlmUsage(
        call_id="c1", at=AT, model="gemini-test", purpose="judge", status=LlmCallStatus.OK
    )
    judged = (
        Judged(output=output, problems=[], unverified=unverified or {})
        if output is not None
        else Judged(output=None, problems=[], unverified={}, error="not the expected JSON")
    )
    return TrialResult(
        job_id=job_id, model="gemini-test", judge_key=key, usage=usage,
        aliases={f"C0{i}": f"TAL-00{i}" for i in range(1, 6)},
        judged=judged, raw_text="{}",
    )  # fmt: skip


def top(alias: str, score: int, quote: str = "Build dashboards") -> TopMatch:
    return TopMatch(
        alias=alias, fit_score=score, requirements_met=["Power BI"],
        requirements_missing=["", "French"],
        red_flags=[RedFlag(kind="seniority", detail="one level above")],
        evidence=[Evidence(source="job", quote=quote)],
    )  # fmt: skip


def verdict(alias: str, v: str, reason: str = "r") -> ContractorVerdict:
    return ContractorVerdict(alias=alias, verdict=v, reason=reason)  # type: ignore[arg-type]


def ids(output: JudgeOutput) -> list[tuple[str, str, bool]]:
    judged = judged_job(result(output), RULES)
    assert judged is not None
    return [(m.talent_id, m.verdict.value, m.detailed) for m in judged.matches]


FRENCH = JudgeOutput(
    no_fit=False,
    verdicts=[
        verdict("C01", "partial", "Tableaux de bord, pas de SQL."),
        verdict("C02", "fit", "Analyste de données confirmé."),
        verdict("C03", "no", "Recruteur."),
    ],
    top=[top("C02", 88, "Concevoir des tableaux de bord"), top("C01", 61)],
)


# ---- the shortlist rule ----


def test_job_with_a_fit_keeps_fits_only() -> None:
    judged = judged_job(result(FRENCH, unverified={"C02": ["une citation"]}), RULES)
    assert judged is not None and judged.status is JobStatus.MATCHED
    [only] = judged.matches  # the partial C01 is dropped
    assert (only.talent_id, only.rank, only.verdict, only.fit_score, only.detailed) == (
        "TAL-002", 1, LlmVerdict.FIT, 88, True,
    )  # fmt: skip
    assert only.reason == "Analyste de données confirmé."
    assert only.match_id == "k1-TAL-002"
    assert only.requirements_missing == ["French"]  # blank items dropped
    assert only.red_flags == ["seniority: one level above"]
    assert only.evidence == ["job: Concevoir des tableaux de bord"]
    assert only.unverified_evidence
    assert only.created_at == AT and only.model == "gemini-test"


def test_at_most_three_fits_in_geminis_order_then_undescribed() -> None:
    many = JudgeOutput(
        no_fit=False,
        verdicts=[
            verdict("C01", "fit"),
            verdict("C02", "fit"),
            verdict("C03", "partial"),
            verdict("C04", "fit"),
            verdict("C05", "fit"),
        ],  # fmt: skip
        top=[top("C04", 90), top("C03", 85), top("C02", 70)],
    )
    # Described fits first (C04, C02), then undescribed fits in verdict order (C01); C05 is 4th.
    assert ids(many) == [
        ("TAL-004", "fit", True), ("TAL-002", "fit", True), ("TAL-001", "fit", False),
    ]  # fmt: skip


def test_job_with_only_partials_keeps_the_best_one() -> None:
    partials = JudgeOutput(
        no_fit=False,
        verdicts=[verdict("C01", "partial"), verdict("C02", "partial"), verdict("C03", "no")],
        top=[top("C02", 64), top("C01", 58)],
    )
    judged = judged_job(result(partials), RULES)
    assert judged is not None and judged.status is JobStatus.MATCHED
    assert [(m.talent_id, m.verdict, m.fit_score) for m in judged.matches] == [
        ("TAL-002", LlmVerdict.PARTIAL, 64)
    ]


def test_undescribed_partial_is_kept_when_it_is_the_only_candidate() -> None:
    # Self-contradicting answer seen in real runs: "no_fit" with a partial verdict.
    contradiction = JudgeOutput(
        no_fit=True,
        verdicts=[
            verdict("C01", "no", "Designer."),
            verdict("C02", "partial", "Recrutement, mais pas en TI."),
            verdict("C03", "partial", "Some recruiting."),
        ],  # fmt: skip
        top=[],
    )
    judged = judged_job(result(contradiction), RULES)
    assert judged is not None and judged.status is JobStatus.MATCHED
    [only] = judged.matches
    assert (only.talent_id, only.detailed, only.fit_score) == ("TAL-002", False, None)
    assert only.reason == "Recrutement, mais pas en TI."
    assert only.evidence == [] and not only.unverified_evidence


def test_undescribed_fit_beats_described_partials() -> None:
    contradiction = JudgeOutput(
        no_fit=True,
        verdicts=[verdict("C01", "partial"), verdict("C02", "fit", "Senior IT recruiter.")],
        top=[top("C01", 60)],
    )
    assert ids(contradiction) == [("TAL-002", "fit", False)]


def test_no_fit_and_no_partial_means_no_match() -> None:
    nobody = JudgeOutput(no_fit=True, verdicts=[verdict("C01", "no", "Autre métier.")], top=[])
    judged = judged_job(result(nobody), RULES)
    assert judged is not None
    assert (judged.status, judged.matches) == (JobStatus.NO_MATCH, [])


def test_numbers_come_from_settings() -> None:
    wider = LlmShortlistSettings(max_fits=1, partials_when_no_fit=0)
    partials = JudgeOutput(no_fit=False, verdicts=[verdict("C01", "partial")], top=[top("C01", 60)])
    judged = judged_job(result(partials), wider)
    assert judged is not None and judged.matches == [] and judged.status is JobStatus.NO_MATCH


def test_unusable_answer_leaves_the_job_alone() -> None:
    assert judged_job(result(None), RULES) is None


def test_top_entries_judged_no_or_unknown_are_skipped() -> None:
    odd = JudgeOutput(
        no_fit=False,
        verdicts=[verdict("C01", "no", "Sales, not recruiting."), verdict("C02", "fit")],
        top=[top("C09", 90), top("C01", 70), top("C02", 65)],
    )
    assert ids(odd) == [("TAL-002", "fit", True)]


# ---- storage and export ----


def test_rows_are_added_once_and_current_follows_the_judge_key(store: CsvStore) -> None:
    repo = CsvLlmMatchRepository(store)
    rows = judged_job(result(FRENCH, key="old"), RULES)
    assert rows is not None
    assert len(repo.add_many(rows.matches)) == 1
    assert repo.add_many(rows.matches) == []  # re-run: nothing new
    nobody = JudgeOutput(no_fit=True, verdicts=[], top=[])
    newer = judged_job(result(nobody, key="new"), RULES)
    assert newer is not None and newer.matches == []
    assert [m.talent_id for m in repo.current({"paste:1": "old"})["paste:1"]] == ["TAL-002"]
    # Re-judged (new key) as no fit: the old shortlist is no longer current.
    assert repo.current({"paste:1": "new"}) == {"paste:1": []}


def test_export_has_names_and_a_row_for_jobs_without_matches(
    make_job: Callable[..., Job], tmp_path: Path
) -> None:
    fit = judged_job(result(FRENCH), RULES)
    partial_only = judged_job(
        result(JudgeOutput(no_fit=True, verdicts=[verdict("C03", "partial", "Adjacent.")], top=[]),
               job_id="paste:4", key="k4"),
        RULES,
    )  # fmt: skip
    assert fit is not None and partial_only is not None
    jobs = [
        make_job(job_id="paste:1", source_id="1", title_norm="Analyste de données"),
        make_job(job_id="paste:2", source_id="2", title_raw='=HYPERLINK("x") Recruiter'),
        make_job(job_id="paste:3", source_id="3", title_norm="Data Analyst"),
        make_job(job_id="paste:4", source_id="4", title_norm="UX Writer"),
    ]
    profiles = {
        "TAL-002": ContractorProfile(
            talent_id="TAL-002", talent_name="Camille Roy", rate_expectation="80",
            standardized_headline="Analyste de données",
        )
    }  # fmt: skip
    current = {"paste:1": fit.matches, "paste:2": [], "paste:4": partial_only.matches}
    rows = build_export_rows(current, jobs, profiles)
    assert [(r.job_id, r.result, r.verdict, r.detail) for r in rows] == [
        ("paste:1", "match", "fit", "full"),
        ("paste:2", "no match", None, None),
        ("paste:3", "not judged yet", None, None),
        ("paste:4", "match", "partial", "reason only"),
    ]
    assert rows[0].talent_name == "Camille Roy" and rows[3].talent_name is None
    assert rows[0].evidence_check == "verified" and rows[3].evidence_check is None
    dest = tmp_path / "out" / "llm.csv"
    assert write_llm_export(rows, dest) == 4
    text = dest.read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in text  # formula neutralized
