"""Comparison of Gemini verdicts with blind labels. Synthetic."""

from datetime import UTC, datetime
from pathlib import Path

from jobmatch.llm.compare import LabelledPair, compare, read_labels
from jobmatch.llm.prompt import JudgeOutput
from jobmatch.llm.trial import Judged, TrialResult
from jobmatch.schemas import LlmCallStatus, LlmUsage

AT = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


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
    comparison, rows = compare(labels, results)

    assert comparison.jobs_judged == 2 and comparison.labelled_pairs == 4
    assert comparison.confusion["fit"]["fit"] == 1
    assert comparison.confusion["no"] == {"fit": 0, "partial": 1, "no": 2, "missing": 0}
    assert comparison.exact_agreement == 0.75
    # Gemini surfaces T1 (fit) and T2 (no, as partial) in J1.
    assert comparison.gemini_surfaced.by_label["fit"] == (1, 1)
    assert comparison.gemini_surfaced.by_label["no"] == (1, 3)
    # J1: fit T1 vs no T2 and T3 -> Gemini ranks the fit above both.
    assert comparison.within_job_pairs == 2
    assert comparison.within_job_gemini == 1.0
    assert comparison.jobs.with_labelled_fit == 1
    assert comparison.jobs.gemini_nothing_and_no_fit == 1  # J2: no_fit, rightly
    assert {r.job_id for r in rows} == {"J1", "J2"}
