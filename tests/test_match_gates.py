"""Rule checks and reviewer flags (match/gates.py). Profiles are synthetic."""

from collections.abc import Callable
from typing import Any

import pytest

from conftest import CONFIG_DIR
from jobmatch.config import MatchingSettings, Settings, load_normalization_rules
from jobmatch.match.gates import (
    contractor_province,
    contractor_rate,
    flags,
    hourly_pay_max,
    location_check,
    seniority_check,
)
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.schemas import ContractorProfile, Job, Language, PayUnit, RemoteType

SENIORITY = SeniorityDetector(load_normalization_rules(CONFIG_DIR).seniority)


def profile(**fields: Any) -> ContractorProfile:
    base: dict[str, Any] = {
        "talent_id": "TAL-1",
        "talent_name": "Sample Person",
        "location": "Toronto, ON",
        "executive_summary": "SYNTHETIC summary.",
        "rate_expectation": "Industry Standard",
    }
    base.update(fields)
    return ContractorProfile(**base)


@pytest.fixture
def matching(settings: Settings) -> MatchingSettings:
    return settings.matching


# ---- seniority ----


@pytest.mark.parametrize(
    ("text", "levels"),
    [
        ("Senior / Director", ["senior", "executive"]),
        ("Mid-level specialist", ["intermediate"]),
        ("Sénior / Directrice de création", ["senior", "executive"]),
        ("Executive / VP / Advisor", ["executive"]),
        ("Founder", []),
        (None, []),
    ],
)
def test_contractor_levels_from_free_text(text: str | None, levels: list[str]) -> None:
    assert SENIORITY.levels(text) == levels


def test_job_title_still_takes_the_highest_level() -> None:
    assert SENIORITY.detect("Senior Director, Marketing") == "executive"


def test_under_qualified_names_both_levels() -> None:
    reason = seniority_check("senior", ["junior"], max_gap=1)
    assert reason == "seniority: under-qualified (job senior, contractor junior)"


def test_over_qualified_is_flagged_too() -> None:
    reason = seniority_check("intermediate", ["executive"], max_gap=1)
    assert reason == "seniority: over-qualified (job intermediate, contractor executive)"


def test_any_held_level_close_enough_passes() -> None:
    # "Senior / Director" is one step from lead through either level.
    assert seniority_check("lead", ["senior", "executive"], max_gap=1) is None
    assert seniority_check("senior", ["intermediate"], max_gap=1) is None


def test_gap_is_a_setting() -> None:
    assert seniority_check("senior", ["junior"], max_gap=2) is None
    assert seniority_check("senior", ["intermediate"], max_gap=0) is not None


def test_unknown_seniority_passes() -> None:
    assert seniority_check(None, ["junior"], max_gap=1) is None
    assert seniority_check("executive", [], max_gap=1) is None


# ---- location ----


@pytest.mark.parametrize(
    ("location", "province"),
    [("Toronto, ON", "CA-ON"), ("Montréal, qc", "CA-QC"), ("Canada", None), (None, None)],
)
def test_contractor_province(location: str | None, province: str | None) -> None:
    assert contractor_province(location) == province


def test_location_always_passes_while_all_contract_work_is_remote(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    assert matching.assume_all_remote is True  # user decision 2026-10-26
    job = make_job(remote_type=RemoteType.ONSITE, province="CA-BC", city="Vancouver")
    assert location_check(job, profile(location="Toronto, ON"), matching) is None


def test_province_check_when_turned_on(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    on = matching.model_copy(update={"assume_all_remote": False})
    hybrid_bc = make_job(remote_type=RemoteType.HYBRID, province="CA-BC")
    assert (
        location_check(hybrid_bc, profile(location="Toronto, ON"), on)
        == "location: hybrid job in CA-BC, contractor in CA-ON"
    )
    assert location_check(hybrid_bc, profile(location="Vancouver, BC"), on) is None
    remote = make_job(remote_type=RemoteType.REMOTE, province="CA-BC")
    assert location_check(remote, profile(location="Toronto, ON"), on) is None
    assert location_check(hybrid_bc, profile(location="Canada"), on) is None  # unknown passes


# ---- pay and rate ----


def test_yearly_pay_becomes_hourly(make_job: Callable[..., Job]) -> None:
    job = make_job(pay_min=70000, pay_max=80000, pay_unit=PayUnit.YEAR)
    assert hourly_pay_max(job, 2080) == pytest.approx(38.46, abs=0.01)
    daily = make_job(pay_min=600, pay_unit=PayUnit.DAY)  # single amount: pay_min only
    assert hourly_pay_max(daily, 2080) == pytest.approx(75)
    assert hourly_pay_max(make_job(), 2080) is None


def test_rate_default_text_is_not_a_number() -> None:
    assert contractor_rate(profile(rate_expectation="52.5")) == 52.5
    assert contractor_rate(profile(rate_expectation="Industry Standard")) is None


def test_flags_are_information_only(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = make_job(pay_min=70000, pay_max=80000, pay_unit=PayUnit.YEAR, language=Language.FR)
    notes = flags(job, profile(rate_expectation="150"), matching)
    assert notes == [
        "rate above posted pay (contractor $150/h, job up to $38/h)",
        "posting in French",
    ]
    # Within the tolerance, or no figure on either side: no rate note.
    assert (
        flags(
            job.model_copy(update={"language": Language.EN}),
            profile(rate_expectation="45"),
            matching,
        )
        == []
    )
    assert flags(make_job(), profile(), matching) == []


def test_thin_profile_is_noted(make_job: Callable[..., Job], matching: MatchingSettings) -> None:
    thin = profile(executive_summary=None, core_strengths=[])
    assert "thin profile (no summary or strengths)" in flags(make_job(), thin, matching)
