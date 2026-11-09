"""Cached Gemini judgments -> llm_matches rows (shortlist rule), job status, current shortlist
and export. No network, no API."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobmatch.config import LlmShortlistSettings
from jobmatch.llm.export import build_export_rows, write_llm_export
from jobmatch.llm.matches import drop_aliases, judged_job, plain_words
from jobmatch.llm.prompt import (
    ContractorVerdict,
    JudgeOutput,
    MeetRow,
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
        alias=alias, fit_score=score,
        meets=[MeetRow(skill="Power BI", requirement_quote=quote, requirement="Dashboards")],
        requirements_missing=["", "French"],
        red_flags=[RedFlag(kind="seniority", detail="one level above")],
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
    assert only.requirements_met == ["Power BI → Dashboards"]  # skill → requirement summary
    assert only.evidence == []  # v10: no separate evidence quotes
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
        top=[top("C04", 90), top("C03", 60), top("C02", 70)],
    )
    # Described fits first (C04 90, C02 70; C03's 60 makes it a partial), then undescribed
    # fits in verdict order (C01); C05 would be 4th.
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


def test_unknown_or_low_scored_deep_reviews_are_skipped() -> None:
    odd = JudgeOutput(
        no_fit=False,
        verdicts=[verdict("C01", "no", "Sales, not recruiting."), verdict("C02", "fit")],
        top=[top("C09", 90), top("C01", 35), top("C02", 75)],
    )
    # C09 is not in the roster; C01's 35 means "no" (the score decides).
    assert ids(odd) == [("TAL-002", "fit", True)]


def test_a_fit_scored_below_the_fit_range_is_kept_as_partial() -> None:
    """The score decides (user rule 2026-11-08): fit 65 -> partial; fit 70 stays fit."""
    low = JudgeOutput(
        no_fit=False,
        verdicts=[verdict("C01", "fit", "Rédactrice UX."), verdict("C02", "fit", "UX writer.")],
        top=[top("C01", 65), top("C02", 70)],
    )
    assert ids(low) == [("TAL-002", "fit", True)]  # a fit exists: partials are dropped
    alone = JudgeOutput(
        no_fit=False, verdicts=[verdict("C01", "fit", "Rédactrice UX.")], top=[top("C01", 65)]
    )
    assert ids(alone) == [("TAL-001", "partial", True)]  # shown as the best partial


def test_the_score_sets_the_verdict_in_both_directions() -> None:
    """User rule 2026-11-08: 70+ fit, 40-69 partial, below 40 not shown, whatever verdict
    the model wrote (a 'partial' scored 75 was shown green with a 'partial' label)."""
    output = JudgeOutput(
        no_fit=False,
        verdicts=[verdict(f"C0{i}", "partial") for i in range(1, 4)],
        top=[
            deep("C01", 75, "partial", "Chef de projet numérique, pas en TI."),
            deep("C02", 35, "fit", "Different field."),
            deep("C03", 69, "fit", "Close but missing the core."),
        ],
    )  # fmt: skip
    assert ids(output) == [("TAL-001", "fit", True)]  # 75 -> fit; 35 -> not shown
    without = output.model_copy(update={"top": output.top[1:]})
    assert ids(without) == [("TAL-003", "partial", True)]  # 69 -> partial


def deep(alias: str, score: int, final: str, reason: str = "") -> TopMatch:
    return TopMatch(
        alias=alias, reason=reason, meets=[], requirements_missing=[], red_flags=[],
        fit_score=score, verdict=final,  # type: ignore[arg-type]
    )  # fmt: skip


FR_REASON = "Rédacteur UX principal avec dix ans d'expérience. Rien d'important ne manque."
PY_REASON = "Matches every point except Python. Python is the core of this project."


def test_the_deep_review_verdict_and_reason_win_over_the_screening() -> None:
    output = JudgeOutput(
        no_fit=False,
        verdicts=[
            verdict("C01", "partial", "Rédacteur, à voir."),
            verdict("C02", "fit", "Strong developer."),
            verdict("C03", "fit", "Data analyst."),
        ],
        top=[
            deep("C01", 88, "fit", FR_REASON),
            deep("C02", 55, "partial", PY_REASON),
            deep("C03", 30, "no", "Different profession after a closer look."),
        ],
    )  # fmt: skip
    judged = judged_job(result(output), RULES)
    assert judged is not None
    rows = judged.matches
    assert [(m.talent_id, m.verdict.value) for m in rows] == [("TAL-001", "fit")]
    assert rows[0].reason is not None and rows[0].reason.startswith("Rédacteur UX principal")
    # Without the fit, the best partial is the deep-reviewed Python case, not the screening.
    no_fit = output.model_copy(update={"top": output.top[1:]})
    judged = judged_job(result(no_fit), RULES)
    assert judged is not None
    assert [(m.talent_id, m.verdict.value) for m in judged.matches] == [("TAL-002", "partial")]
    assert judged.matches[0].reason == PY_REASON


def test_the_shown_three_are_ranked_by_final_verdict_then_score() -> None:
    output = JudgeOutput(
        no_fit=False,
        verdicts=[verdict(f"C0{i}", "partial") for i in range(1, 6)],
        top=[
            deep("C01", 60, "partial"), deep("C02", 78, "fit"), deep("C03", 95, "fit"),
            deep("C04", 66, "partial"), deep("C05", 85, "fit"),
        ],
    )  # fmt: skip
    assert ids(output) == [
        ("TAL-003", "fit", True), ("TAL-005", "fit", True), ("TAL-002", "fit", True),
    ]  # fmt: skip


def test_meets_rows_store_the_skill_and_the_requirement_summary() -> None:
    output = JudgeOutput.model_validate(
        {
            "no_fit": False,
            "verdicts": [{"alias": "C01", "verdict": "fit", "reason": "Analyste."}],
            "top": [
                {
                    "alias": "C01", "fit_score": 92,
                    "meets": [
                        {"skill": "Tableaux de bord Power BI",
                         "requirement_quote": "Concevoir et maintenir des tableaux de bord "
                                              "Power BI pour les équipes de direction",
                         "requirement": "Tableaux de bord Power BI"},
                        {"skill": "Managed 40+ cross-functional staff",
                         "requirement_quote": "lead a team",
                         "requirement": "lead a team"},
                    ],
                    "requirements_missing": [], "red_flags": [],
                }
            ],
        }
    )  # fmt: skip
    judged = judged_job(result(output), RULES)
    assert judged is not None
    assert judged.matches[0].requirements_met == [
        "Tableaux de bord Power BI → Tableaux de bord Power BI",
        "Managed 40+ cross-functional staff → lead a team",
    ]


def test_answers_saved_before_v10_still_read() -> None:
    v6 = TopMatch.model_validate(
        {
            "alias": "C01", "fit_score": 80,
            "requirements_met": [
                {"requirement": "Power BI", "card_quote": "Tableaux de bord Power BI"},
                "SQL",  # up to v5: plain text, no quote
            ],
            "requirements_missing": [], "red_flags": [],
            "evidence": [{"source": "job", "quote": "Build dashboards"}],
        }
    )  # fmt: skip
    assert [(r.skill, r.requirement) for r in v6.meets] == [("Power BI", ""), ("SQL", "")]
    v9 = TopMatch.model_validate(
        {
            "alias": "C01", "fit_score": 80, "reason": "r", "verdict": "fit",
            "proof_points": [{"point": "Led 40+ products", "profile_quote": "led 40+ products"}],
            "requirements_missing": [], "red_flags": [], "evidence": [],
        }
    )  # fmt: skip
    assert [(r.skill, r.requirement) for r in v9.meets] == [("Led 40+ products", "")]


def test_technical_words_become_plain_english() -> None:
    assert plain_words("Card does not mention SEO.") == "Their profile does not mention SEO."
    assert plain_words("Not shown in the card or the summary.") == (
        "Not shown in their profile or the summary."
    )
    assert plain_words("The contractor's card lists Hootsuite.") == (
        "Their profile lists Hootsuite."
    )
    assert plain_words("Each alias was judged.") == "Each contractor was judged."
    assert plain_words("Discarded cardboard samples") == "Discarded cardboard samples"
    # Real job words are left alone.
    assert plain_words("Rédactrice avec cartes de visite") == "Rédactrice avec cartes de visite"


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


# ---- no alias or ID in the text a person reads (user rule 2026-11-08) ----


def test_aliases_are_dropped_from_the_models_text() -> None:
    aliases = {"C02": "TAL-BC-002", "C34": "TAL-ON-034"}
    assert drop_aliases("C34 is a senior writer with health content.", aliases) == (
        "Senior writer with health content."
    )
    assert drop_aliases("C34 has 12 years of copywriting.", aliases) == "12 years of copywriting."
    assert drop_aliases("C34 est une rédactrice principale.", aliases) == "Rédactrice principale."
    assert drop_aliases("Strong fit; C02 also knows SEO.", aliases) == (
        "Strong fit; this contractor also knows SEO."
    )
    assert drop_aliases("C02 knows Power BI", aliases) == "This contractor knows Power BI"
    # Not an alias of this roster, or part of a longer word: left alone.
    assert drop_aliases("Licence C99, poste ABC02, code C3", aliases) == (
        "Licence C99, poste ABC02, code C3"
    )
    for text in ("C34 is a writer.", "Rédactrice (C34)."):  # never a talent ID either
        assert "TAL-" not in drop_aliases(text, aliases)


def test_rows_name_nobody() -> None:
    output = JudgeOutput(
        no_fit=False,
        verdicts=[verdict("C02", "fit", "C02 est analyste de données confirmée.")],
        top=[
            TopMatch(
                alias="C02", fit_score=91,
                meets=[MeetRow(skill="C02 knows Power BI", requirement_quote="Power BI",
                               requirement="Power BI")],
                requirements_missing=["French (C02 lists English only)"],
                red_flags=[RedFlag(kind="other", detail="C02 is one level above")],
            )
        ],
    )  # fmt: skip
    judged = judged_job(result(output), RULES)
    assert judged is not None
    row = judged.matches[0]
    assert row.talent_id == "TAL-002"  # the row still knows who it is
    assert row.reason == "Analyste de données confirmée."
    assert row.requirements_met == ["This contractor knows Power BI → Power BI"]
    assert row.requirements_missing == ["French (this contractor lists English only)"]
    assert row.red_flags == ["other: this contractor is one level above"]
