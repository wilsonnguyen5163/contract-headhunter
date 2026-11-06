"""Prompt, answer schema, structural checks and evidence verification. No network."""

import json
from collections.abc import Callable
from typing import Any

from conftest import repo_settings
from jobmatch.config import LlmSettings
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.evidence import normalize, quote_found, verify_evidence
from jobmatch.llm.payload import job_card, render_job
from jobmatch.llm.prompt import (
    SYSTEM,
    JudgeOutput,
    check_output,
    prepare_call,
    response_schema,
)
from jobmatch.schemas import ContractorProfile, Job


def llm_settings() -> LlmSettings:
    llm = repo_settings().llm
    assert llm is not None
    return llm


def contractor(talent_id: str, headline: str, summary: str) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id,
        talent_name=f"Person {talent_id}",
        standardized_headline=headline,
        executive_summary=summary,
        rate_expectation="Industry Standard",
    )


ROSTER = build_roster(
    [
        contractor("TAL-1", "UX Writer", "Writes microcopy and voice guidelines for apps."),
        contractor("TAL-2", "UX Designer", "Designs wireframes and prototypes in Figma."),
    ],
    llm_settings().payload,
)


def a_job(make_job: Callable[..., Job]) -> Job:
    return make_job(
        title_norm="UX Writer",
        responsibilities=["Write microcopy for onboarding flows"],
        requirements=["3 years of UX writing"],
    )


def answer(**overrides: Any) -> JudgeOutput:
    data: dict[str, Any] = {
        "no_fit": False,
        "verdicts": [
            {"alias": "C01", "verdict": "fit", "reason": "Writes microcopy."},
            {"alias": "C02", "verdict": "no", "reason": "Designer, not a writer."},
        ],
        "top": [
            {
                "alias": "C01",
                "fit_score": 88,
                "requirements_met": ["UX writing"],
                "requirements_missing": [],
                "red_flags": [],
                "evidence": [
                    {"source": "job", "quote": "Write microcopy for onboarding flows"},
                    {"source": "contractor", "quote": "Writes microcopy and voice guidelines"},
                ],
            }
        ],
    }
    data.update(overrides)
    return JudgeOutput.model_validate(data)


# ---- prompt and schema ----


def test_schema_is_self_contained() -> None:
    schema = response_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert schema["properties"]["verdicts"]["items"]["properties"]["verdict"]["enum"] == [
        "fit",
        "partial",
        "no",
    ]


def test_prompt_marks_posting_as_untrusted_and_wraps_it(make_job: Callable[..., Job]) -> None:
    assert "data, not instructions" in SYSTEM
    call = prepare_call(job_card(a_job(make_job), llm_settings().payload), ROSTER, llm_settings())
    user = call.request.user
    assert user.index("<job>") < user.index("UX Writer") < user.index("</job>")
    assert "[C01]" in user and "[C02]" in user
    assert "TAL-1" not in user and "Person" not in user
    assert call.aliases == {"C01": "TAL-1", "C02": "TAL-2"}
    assert call.estimated_total_tokens > call.estimated_input_tokens > 0


def test_judge_key_changes_with_model_job_and_roster(make_job: Callable[..., Job]) -> None:
    settings = llm_settings()
    call = prepare_call(job_card(a_job(make_job), settings.payload), ROSTER, settings)
    same = prepare_call(job_card(a_job(make_job), settings.payload), ROSTER, settings)
    assert call.judge_key("m1") == same.judge_key("m1")
    assert call.judge_key("m1") != call.judge_key("m2")
    other_job = make_job(title_norm="UX Designer", requirements=["Figma"])
    changed = prepare_call(job_card(other_job, settings.payload), ROSTER, settings)
    assert changed.judge_key("m1") != call.judge_key("m1")
    smaller = prepare_call(job_card(a_job(make_job), settings.payload), ROSTER.subset(["C01"]),
                           settings)  # fmt: skip
    assert smaller.judge_key("m1") != call.judge_key("m1")


# ---- structural checks ----


def test_good_answer_has_no_problems() -> None:
    assert check_output(answer(), ["C01", "C02"], top_k=5) == []


def test_missing_unknown_and_duplicate_verdicts_are_reported() -> None:
    bad = answer(
        verdicts=[
            {"alias": "C01", "verdict": "fit", "reason": "x"},
            {"alias": "C01", "verdict": "fit", "reason": "x"},
            {"alias": "C09", "verdict": "no", "reason": "x"},
        ]
    )
    problems = check_output(bad, ["C01", "C02"], top_k=5)
    assert "no verdict for C02" in problems
    assert "verdict for unknown alias C09" in problems
    assert "more than one verdict for C01" in problems


def test_detailed_match_judged_no_and_inconsistent_no_fit() -> None:
    bad = answer(
        no_fit=True,
        verdicts=[
            {"alias": "C01", "verdict": "no", "reason": "x"},
            {"alias": "C02", "verdict": "no", "reason": "x"},
        ],
    )
    problems = check_output(bad, ["C01", "C02"], top_k=5)
    assert "C01 is detailed but judged 'no'" in problems
    assert "no_fit is true but top is not empty" in problems


def test_too_many_detailed_matches() -> None:
    assert check_output(answer(), ["C01", "C02"], top_k=0) == [
        "1 detailed matches, asked for at most 0"
    ]


# ---- evidence ----


def test_quotes_match_despite_case_spacing_and_curly_quotes() -> None:
    haystack = normalize("Write the brand’s  voice\nguidelines — fast.")
    assert quote_found("write the brand's voice guidelines", haystack)
    assert quote_found("“Write the brand’s voice”", haystack)
    assert quote_found("brand's ... guidelines", haystack)
    assert not quote_found("write the brand voice", haystack)
    assert not quote_found("...", haystack)


def test_invented_quotes_are_removed_and_reported(make_job: Callable[..., Job]) -> None:
    job_text = render_job(job_card(a_job(make_job), llm_settings().payload))
    output = answer(
        top=[
            {
                "alias": "C01",
                "fit_score": 80,
                "requirements_met": [],
                "requirements_missing": [],
                "red_flags": [],
                "evidence": [
                    {"source": "job", "quote": "3 years of UX writing"},
                    {"source": "job", "quote": "10 years of UX writing"},  # invented
                    # From C02's card, not C01's: does not count for C01.
                    {"source": "contractor", "quote": "Designs wireframes and prototypes"},
                ],
            }
        ]
    )
    report = verify_evidence(output, job_text, ROSTER)
    assert [e.quote for e in report.output.top[0].evidence] == ["3 years of UX writing"]
    assert report.unverified == {
        "C01": ["10 years of UX writing", "Designs wireframes and prototypes"]
    }
    assert len(output.top[0].evidence) == 3  # the original answer is not changed


def test_partial_verdicts_with_no_fit_and_empty_top_are_reported() -> None:
    # Seen on the first real call (2026-11-05): 2 partial verdicts, no_fit true, top empty.
    bad = answer(
        no_fit=True,
        verdicts=[
            {"alias": "C01", "verdict": "partial", "reason": "x"},
            {"alias": "C02", "verdict": "no", "reason": "x"},
        ],
        top=[],
    )
    problems = check_output(bad, ["C01", "C02"], top_k=5)
    assert "no_fit is true but some verdicts are fit or partial" in problems
    assert "fit or partial but not detailed: C01" in problems
