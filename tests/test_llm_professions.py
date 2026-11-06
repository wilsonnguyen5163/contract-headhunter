"""Profession suggestions: schema, parsing, checks against professions.yaml, and applying
them to contractors.csv with a backup. Invented people only, no network."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import CONFIG_DIR, repo_settings
from jobmatch.config import load_professions
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.professions import (
    apply_suggestions,
    parse_answer,
    prepare_profession_call,
    read_suggestions,
    response_schema,
    write_suggestions,
)
from jobmatch.schemas import ContractorProfile
from jobmatch.storage.csv_store import CsvContractorProfileRepository, export_csv

PROFESSIONS = load_professions(CONFIG_DIR)
assert PROFESSIONS is not None
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


def profile(talent_id: str, headline: str) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id,
        talent_name=f"Person {talent_id}",
        standardized_headline=headline,
        executive_summary="Synthetic summary.",
        rate_expectation="Industry Standard",
    )


PROFILES = [profile("TAL-1", "Content Writer"), profile("TAL-2", "Rédactrice et réviseure")]


def roster():
    llm = repo_settings().llm
    assert llm is not None
    return build_roster(PROFILES, llm.payload)


def pick(alias: str, primary: str, secondary: list[str] | None = None) -> dict[str, Any]:
    return {
        "alias": alias,
        "primary_profession": primary,
        "secondary_professions": secondary or [],
        "confidence": "high",
        "reason": "Headline says so.",
    }


def test_schema_limits_answers_to_the_profession_list() -> None:
    assert PROFESSIONS is not None
    schema = response_schema(PROFESSIONS.professions)
    item = schema["properties"]["picks"]["items"]
    assert set(item["properties"]["primary_profession"]["enum"]) == set(PROFESSIONS.professions)
    assert item["properties"]["secondary_professions"]["maxItems"] == 2


def test_prompt_lists_professions_and_anonymized_cards() -> None:
    llm = repo_settings().llm
    assert llm is not None and PROFESSIONS is not None
    call = prepare_profession_call(roster(), PROFESSIONS, llm)
    assert "- content writing and editing:" in call.request.user
    assert "[C01]" in call.request.user and "[C02]" in call.request.user
    assert "Person" not in call.request.user and "TAL-1" not in call.request.user
    assert call.estimated_total_tokens > call.estimated_input_tokens


def test_answer_is_mapped_back_to_talent_ids() -> None:
    assert PROFESSIONS is not None
    text = json.dumps(
        {
            "picks": [
                pick("C01", "content writing and editing", ["digital and performance marketing"]),
                pick("C02", "content writing and editing"),
            ]
        }
    )
    parsed = parse_answer(text, roster(), PROFESSIONS, model="m", now=NOW)
    assert parsed.problems == []
    assert [s.talent_id for s in parsed.suggestions] == ["TAL-1", "TAL-2"]
    assert parsed.suggestions[0].secondary_professions == ["digital and performance marketing"]


def test_unknown_names_missing_and_unknown_aliases_are_reported() -> None:
    assert PROFESSIONS is not None
    text = json.dumps(
        {
            "picks": [
                pick("C01", "astronaut"),
                pick("C09", "events"),
                pick("C02", "events", ["events", "wizardry"]),
            ]
        }
    )
    parsed = parse_answer(text, roster(), PROFESSIONS, model="m", now=NOW)
    assert [s.talent_id for s in parsed.suggestions] == ["TAL-2"]
    assert parsed.suggestions[0].secondary_professions == []  # self and unknown dropped
    assert "C01: unknown profession 'astronaut'" in parsed.problems
    assert "answer for unknown alias C09" in parsed.problems
    assert any("wizardry" in p for p in parsed.problems)


def test_answer_that_is_not_json_is_an_error() -> None:
    assert PROFESSIONS is not None
    with pytest.raises(ValueError, match="expected JSON"):
        parse_answer("not json", roster(), PROFESSIONS, model="m", now=NOW)


def test_suggestions_round_trip_and_apply_with_backup(tmp_path: Path) -> None:
    assert PROFESSIONS is not None
    text = json.dumps(
        {"picks": [pick("C01", "content writing and editing", ["events"]), pick("C02", "events")]}
    )
    parsed = parse_answer(text, roster(), PROFESSIONS, model="m", now=NOW)
    suggestions_csv = tmp_path / "profession_suggestions.csv"
    write_suggestions(parsed.suggestions, suggestions_csv)
    rows = read_suggestions(suggestions_csv)
    assert rows == parsed.suggestions

    contractors = tmp_path / "contractors.csv"
    export_csv(ContractorProfile, PROFILES, contractors)
    report = apply_suggestions(PROFILES, rows, contractors, now=NOW)
    assert (report.updated, report.unchanged) == (2, 0)
    assert report.backup is not None and report.backup.exists()
    after = CsvContractorProfileRepository(contractors, lock_timeout=5).list_all()
    assert after[0].primary_profession == "content writing and editing"
    assert after[0].secondary_professions == ["events"]
    assert after[0].talent_name == "Person TAL-1"  # every other cell kept
    again = apply_suggestions(after, rows, contractors, now=NOW)
    assert (again.updated, again.unchanged) == (0, 2)
