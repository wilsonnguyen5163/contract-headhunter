"""Comparison of Gemini verdicts with blind labels and v0's latest stored run. Synthetic."""

from datetime import UTC, datetime
from pathlib import Path

from jobmatch.llm.compare import (
    LabelledPair,
    V0Run,
    compare,
    latest_v0,
    read_labels,
)
from jobmatch.llm.prompt import JudgeOutput
from jobmatch.llm.trial import Judged, TrialResult
from jobmatch.schemas import Band, LlmCallStatus, LlmUsage, Match

AT = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


def match(job: str, contractor: str, score: float, version: str, when: datetime) -> Match:
    return Match(
        match_id=f"m-{job}-{contractor}-{version}",
        job_id=job,
        contractor_id=contractor,
        facet="profile",
        cosine=0.5,
        score=score,
        band=Band.MEDIUM,
        rank=1,
        scoring_version=version,
        created_at=when,
    )


def result(job: str, verdicts: dict[str, str], top: dict[str, int], no_fit: bool = False):
    aliases = {f"C{i:02d}": t for i, t in enumerate(sorted(verdicts), start=1)}
    alias_of = {t: a for a, t in aliases.items()}
    output = JudgeOutput.model_validate(
        {
            "no_fit": no_fit,
            "verdicts": [
                {"alias": alias_of[t], "verdict": v, "reason": "r"} for t, v in verdicts.items()
            ],
            "top": [
                {
                    "alias": alias_of[t],
                    "fit_score": s,
                    "requirements_met": [],
                    "requirements_missing": [],
                    "red_flags": [],
                    "evidence": [],
                }
                for t, s in top.items()
            ],
        }
    )
    usage = LlmUsage(call_id=job, at=AT, model="m", purpose="judge", status=LlmCallStatus.OK)
    return TrialResult(
        job_id=job,
        model="m",
        judge_key=job,
        usage=usage,
        aliases=aliases,
        judged=Judged(output=output, problems=[], unverified={}),
        raw_text=None,
    )


def test_latest_v0_keeps_only_the_newest_scoring_version() -> None:
    old = datetime(2026, 10, 27, tzinfo=UTC)
    run = latest_v0(
        [match("J1", "T1", 0.6, "old", old), match("J1", "T2", 0.7, "new", AT)],
        [match("J2", "T3", 0.5, "new", AT)],
    )
    assert run.version == "new"
    assert set(run.pairs) == {("J1", "T2"), ("J2", "T3")}
    assert run.pairs[("J2", "T3")].flagged
    assert run.shortlist("J1") == ["T2"] and run.shortlist("J2") == []


def test_read_labels(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(
        'job_id,contractor_id,label,source,reason\nJ1,T1,Fit,ai-agree,"multi\nline"\n',
        encoding="utf-8",
    )
    assert read_labels(path) == [LabelledPair(job_id="J1", contractor_id="T1", label="fit",
                                              source="ai-agree")]  # fmt: skip


def test_comparison_measures() -> None:
    labels = [
        LabelledPair(job_id="J1", contractor_id="T1", label="fit", source="ai-agree"),
        LabelledPair(job_id="J1", contractor_id="T2", label="no", source="ai-agree"),
        LabelledPair(job_id="J1", contractor_id="T3", label="no", source="ai-fit-partial"),
        LabelledPair(job_id="J2", contractor_id="T1", label="no", source="ai-agree"),
        LabelledPair(job_id="J3", contractor_id="T1", label="fit", source="ai-agree"),  # not judged
    ]
    results = [
        result("J1", {"T1": "fit", "T2": "partial", "T3": "no"}, {"T1": 90, "T2": 40}),
        result("J2", {"T1": "no"}, {}, no_fit=True),
    ]
    v0 = V0Run(version="v", created_at=AT, pairs={})
    v0 = latest_v0([match("J1", "T2", 0.7, "v", AT), match("J2", "T1", 0.6, "v", AT)], [])
    comparison, rows = compare(labels, results, v0)

    assert comparison.jobs_judged == 2 and comparison.labelled_pairs == 4
    assert comparison.confusion["fit"]["fit"] == 1
    assert comparison.confusion["no"] == {"fit": 0, "partial": 1, "no": 2, "missing": 0}
    assert comparison.exact_agreement == 0.75
    # Gemini surfaces T1 (fit) and T2 (no, as partial) in J1.
    assert comparison.gemini_surfaced.by_label["fit"] == (1, 1)
    assert comparison.gemini_surfaced.by_label["no"] == (1, 3)
    # v0 surfaces the two "no" pairs and misses the fit.
    assert comparison.v0_surfaced.by_label["fit"] == (0, 1)
    assert comparison.v0_surfaced.by_label["no"] == (2, 3)
    # J1: fit T1 vs no T2 and T3 -> Gemini wins both; v0 loses to T2, ties T3.
    assert comparison.within_job_pairs == 2
    assert comparison.within_job_gemini == 1.0
    assert comparison.within_job_v0_shortlist == 0.25
    assert comparison.jobs.with_labelled_fit == 1
    assert comparison.jobs.gemini_nothing_and_no_fit == 1  # J2: no_fit, rightly
    assert comparison.jobs.v0_nothing_and_no_fit == 0  # v0 shortlisted a "no" in J2
    assert {r.job_id for r in rows} == {"J1", "J2"}
