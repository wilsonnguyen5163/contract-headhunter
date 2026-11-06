"""llm-preview end to end on a temporary data folder: requests are written, nothing is sent,
and no personal detail is in them. Invented people only."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import CONFIG_DIR
from jobmatch.cli import app
from jobmatch.schemas import ContractorProfile, Job
from jobmatch.storage.csv_store import CsvStore, export_csv

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, make_job: Callable[..., Job]) -> Path:
    """A copy of the repo config whose data folder is tmp_path/data."""
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    data = tmp_path / "data"
    store = CsvStore(data, lock_timeout=5)
    store.jobs.append(
        [
            make_job(
                job_id="adzuna:1", source_id="1", title_norm="Content Writer",
                responsibilities=["Write blog posts"], requirements=["2 years writing"],
            ),
            make_job(job_id="adzuna:2", source_id="2", title_norm="UX Designer",
                     requirements=["Figma"]),
        ]
    )  # fmt: skip
    contractor = ContractorProfile(
        talent_id="TAL-9",
        talent_name="Riley Sample",
        location="Halifax, NS",
        standardized_headline="Content Writer",
        executive_summary="Riley writes blog posts. Call 902-555-0100.",
        rate_expectation="80",
        primary_profession="content writing and editing",
    )
    export_csv(ContractorProfile, [contractor], data / "contractors" / "contractors.csv")
    return tmp_path


def invoke(project: Path, *args: str):
    return runner.invoke(app, ["--config-dir", str(project / "config"), "llm-preview", *args])


def test_preview_writes_requests_without_personal_details(project: Path) -> None:
    result = invoke(project, "--job-id", "adzuna:1")
    assert result.exit_code == 0, result.output
    assert "Nothing was sent" in result.output
    folders = list((project / "data" / "llm_preview").iterdir())
    assert len(folders) == 1
    request_file = folders[0] / "adzuna_1.json"
    text = request_file.read_text(encoding="utf-8")
    for secret in ("Riley", "Sample", "Halifax", "TAL-9", "902-555-0100", '"80"'):
        assert secret not in text, secret
    body = json.loads(text)["body"]
    assert "Write blog posts" in body["contents"][0]["parts"][0]["text"]
    aliases = json.loads((folders[0] / "aliases.json").read_text(encoding="utf-8"))
    assert aliases == {"C01": "TAL-9"}


def test_preview_stats_prints_budget_and_writes_nothing(project: Path) -> None:
    result = invoke(project, "--stats")
    assert result.exit_code == 0, result.output
    assert "2 active job(s) · 1 contractor card(s)" in result.output
    assert "Full run: 2 call(s)" in result.output
    assert "Budget today: OK" in result.output
    assert not (project / "data" / "llm_preview").exists()


def test_unknown_job_id_fails(project: Path) -> None:
    result = invoke(project, "--job-id", "nope:1")
    assert result.exit_code == 1
    assert "Not an active job: nope:1" in result.output


# ---- llm-try (phase 4) with a fake provider: nothing reaches the network ----


class FakeGemini:
    model = "fake-model"

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[object] = []

    def generate(self, request):  # type: ignore[no-untyped-def]
        from jobmatch.llm.provider import LlmResponse, TokenUsage

        self.requests.append(request)
        usage = TokenUsage(input_tokens=7000, output_tokens=600, total_tokens=7600)
        return LlmResponse(
            text=self.text, usage=usage, model="fake-model", http_status=200, attempts=1
        )


def fake_answer() -> str:
    return json.dumps(
        {
            "no_fit": False,
            "verdicts": [{"alias": "C01", "verdict": "fit", "reason": "Writes blog posts."}],
            "top": [
                {
                    "alias": "C01",
                    "fit_score": 85,
                    "requirements_met": ["writing"],
                    "requirements_missing": [],
                    "red_flags": [],
                    "evidence": [
                        {"source": "job", "quote": "Write blog posts"},
                        {"source": "contractor", "quote": "writes blog posts"},
                        {"source": "contractor", "quote": "ten years at a newspaper"},
                    ],
                }
            ],
        }
    )


def test_llm_try_logs_usage_and_saves_the_checked_answer(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobmatch.llm.gemini import GeminiProvider

    fake = FakeGemini(fake_answer())
    monkeypatch.setattr(GeminiProvider, "from_settings", classmethod(lambda cls, s: fake))
    result = runner.invoke(
        app, ["--config-dir", str(project / "config"), "llm-try", "--job-id", "adzuna:1"]
    )
    assert result.exit_code == 0, result.output
    assert len(fake.requests) == 1
    assert "total 7,600" in result.output
    assert "1 fit · 0 partial · 0 no" in result.output
    assert "TAL-9  Content Writer" in result.output
    assert "unverified evidence removed for TAL-9: 1 quote(s)" in result.output
    assert "Riley" not in result.output
    usage = (project / "data" / "llm_usage.csv").read_text(encoding="utf-8")
    assert "judge_try" in usage and "adzuna:1" in usage
    saved = next((project / "data" / "llm_results").rglob("adzuna_1.json"))
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["aliases"] == {"C01": "TAL-9"}
    assert len(data["judged"]["output"]["top"][0]["evidence"]) == 2


def test_llm_try_without_a_key_sends_nothing(project: Path) -> None:
    result = runner.invoke(app, ["--config-dir", str(project / "config"), "llm-try"])
    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.output and "Nothing was sent" in result.output
    assert not (project / "data" / "llm_usage.csv").exists()


def test_suggest_professions_writes_suggestions_only(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobmatch.llm.gemini import GeminiProvider

    answer = {
        "picks": [
            {
                "alias": "C01",
                "primary_profession": "communications and public relations",
                "secondary_professions": [],
                "confidence": "high",
                "reason": "Content Writer headline.",
            }
        ]
    }
    fake = FakeGemini(json.dumps(answer))
    monkeypatch.setattr(GeminiProvider, "from_settings", classmethod(lambda cls, s: fake))
    contractors = project / "data" / "contractors" / "contractors.csv"
    before = contractors.read_bytes()
    result = runner.invoke(app, ["--config-dir", str(project / "config"), "suggest-professions"])
    assert result.exit_code == 0, result.output
    assert "TAL-9: communications and public relations [high]" in result.output
    assert contractors.read_bytes() == before  # suggesting never edits the list
    suggestions = next(contractors.parent.glob("profession_suggestions-*.csv"))
    result = runner.invoke(
        app, ["--config-dir", str(project / "config"), "apply-professions", str(suggestions)]
    )
    assert result.exit_code == 0, result.output
    assert "Updated 1 contractor(s)" in result.output
    assert "communications and public relations" in contractors.read_text(encoding="utf-8-sig")
    assert len(list((contractors.parent / "backups").glob("contractors.*.csv"))) == 1
