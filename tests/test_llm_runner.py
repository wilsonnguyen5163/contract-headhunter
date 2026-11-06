"""send(): every call, failed or not, is logged with its tokens. Fake providers, no network."""

from datetime import UTC, datetime

from conftest import repo_settings
from jobmatch.config import LlmSettings
from jobmatch.llm.provider import (
    DailyQuotaError,
    LlmRequest,
    LlmResponse,
    LlmResponseError,
    TokenUsage,
)
from jobmatch.llm.runner import send
from jobmatch.llm.usage import UsageLedger
from jobmatch.schemas import LlmCallStatus
from jobmatch.storage.csv_store import CsvStore

REQUEST = LlmRequest(system="s", user="u", response_schema={"type": "object"})
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


def llm() -> LlmSettings:
    settings = repo_settings().llm
    assert settings is not None
    return settings


class Fake:
    model = "fake"

    def __init__(self, result: LlmResponse | Exception) -> None:
        self.result = result
        self.calls = 0

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ok_response() -> LlmResponse:
    usage = TokenUsage(input_tokens=8000, output_tokens=1200, thinking_tokens=50, total_tokens=9250)
    return LlmResponse(text="{}", usage=usage, model="m", http_status=200, attempts=1)


def test_successful_call_is_logged_with_its_tokens(store: CsvStore) -> None:
    ledger = UsageLedger(store)
    outcome = send(
        Fake(ok_response()), ledger, llm(), REQUEST, purpose="test", job_ids=["a:1"],
        estimated_tokens=9000, clock=lambda: NOW,
    )  # fmt: skip
    assert outcome.ok
    row = ledger.all()[0]
    assert (row.status, row.input_tokens, row.total_tokens) == (LlmCallStatus.OK, 8000, 9250)
    assert row.job_ids == ["a:1"] and row.estimated_tokens == 9000
    assert "total 9,250" in outcome.line and "today 1/" in outcome.line


def test_failed_call_keeps_the_tokens_it_spent(store: CsvStore) -> None:
    spent = TokenUsage(input_tokens=8000, output_tokens=4096, total_tokens=12096)
    error = LlmResponseError("answer cut", usage=spent, http_status=200, attempts=1)
    outcome = send(
        Fake(error), UsageLedger(store), llm(), REQUEST, purpose="test", estimated_tokens=9000,
        clock=lambda: NOW,
    )  # fmt: skip
    assert not outcome.ok
    assert outcome.usage.status == LlmCallStatus.ERROR
    assert outcome.usage.total_tokens == 12096
    assert outcome.usage.note == "answer cut"


def test_daily_quota_is_logged_as_such(store: CsvStore) -> None:
    error = DailyQuotaError("daily limit reached", http_status=429, attempts=1)
    outcome = send(
        Fake(error), UsageLedger(store), llm(), REQUEST, purpose="test", estimated_tokens=9000,
        clock=lambda: NOW,
    )  # fmt: skip
    assert outcome.usage.status == LlmCallStatus.DAILY_QUOTA


def test_call_too_large_is_not_sent(store: CsvStore) -> None:
    fake = Fake(ok_response())
    outcome = send(
        fake, UsageLedger(store), llm(), REQUEST, purpose="test", estimated_tokens=10_000_000,
        clock=lambda: NOW,
    )  # fmt: skip
    assert fake.calls == 0
    assert outcome.usage.status == LlmCallStatus.NOT_SENT
    assert outcome.usage.attempts == 0
