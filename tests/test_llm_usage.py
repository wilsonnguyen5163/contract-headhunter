"""Usage log, Pacific-time day and the daily budget guard."""

from datetime import UTC, datetime, timedelta

from jobmatch.config import LlmBudget, LlmLimits
from jobmatch.llm.usage import (
    DayUsage,
    Pacer,
    UsageLedger,
    check_call,
    check_run,
    day_usage,
    pacific_day_start,
    usage_line,
)
from jobmatch.schemas import LlmCallStatus, LlmUsage
from jobmatch.storage.csv_store import CsvStore

LIMITS = LlmLimits(rpm=10, tpm=250_000, rpd=250)
BUDGET = LlmBudget(drain_warn_share=0.8, call_max_tpm_share=0.5)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def usage(when: datetime, tokens: int = 10_000, attempts: int = 1, **kw: object) -> LlmUsage:
    fields: dict[str, object] = {
        "call_id": f"c{when.timestamp()}{tokens}{attempts}",
        "at": when,
        "model": "gemini-test",
        "purpose": "judge",
        "job_ids": ["adzuna:1"],
        "status": LlmCallStatus.OK,
        "attempts": attempts,
        "total_tokens": tokens,
    }
    fields.update(kw)
    return LlmUsage.model_validate(fields)


# ---- the day starts at midnight Pacific time ----


def test_day_start_in_winter_is_08_utc() -> None:
    assert pacific_day_start(at("2026-11-05T10:00:00")) == at("2026-11-05T08:00:00")
    # 03:00 UTC is still the previous evening in Vancouver.
    assert pacific_day_start(at("2026-11-05T03:00:00")) == at("2026-11-04T08:00:00")


def test_day_start_in_summer_is_07_utc() -> None:
    assert pacific_day_start(at("2026-07-15T12:00:00")) == at("2026-07-15T07:00:00")


def test_day_start_on_daylight_saving_change_days() -> None:
    # 2026-11-01: clocks go back at 2:00 PDT. Midnight was still PDT (07:00 UTC).
    assert pacific_day_start(at("2026-11-01T20:00:00")) == at("2026-11-01T07:00:00")
    # 2026-03-08: clocks go forward at 2:00 PST. Midnight was still PST (08:00 UTC).
    assert pacific_day_start(at("2026-03-08T20:00:00")) == at("2026-03-08T08:00:00")


def test_day_usage_counts_attempts_and_tokens_since_midnight_pacific() -> None:
    now = at("2026-11-05T20:00:00")
    records = [
        usage(at("2026-11-05T07:59:00"), tokens=99_999),  # yesterday, Pacific time
        usage(at("2026-11-05T09:00:00"), tokens=10_000, attempts=2),
        usage(at("2026-11-05T19:00:00"), tokens=5_000),
    ]
    today = day_usage(records, now)
    assert (today.calls, today.requests, today.tokens) == (2, 3, 15_000)


def test_ledger_round_trip(store: CsvStore) -> None:
    ledger = UsageLedger(store)
    ledger.record(usage(at("2026-11-05T09:00:00"), input_tokens=900, output_tokens=100))
    ledger.record(usage(at("2026-11-05T09:01:00"), status=LlmCallStatus.DAILY_QUOTA))
    rows = ledger.all()
    assert [r.status for r in rows] == [LlmCallStatus.OK, LlmCallStatus.DAILY_QUOTA]
    assert rows[0].job_ids == ["adzuna:1"]
    assert ledger.today(at("2026-11-05T10:00:00")).requests == 2


def test_usage_line_reports_tokens_and_the_day() -> None:
    record = usage(
        at("2026-11-05T09:00:00"), input_tokens=8_100, output_tokens=1_200, thinking_tokens=0,
        tokens=9_300, estimated_tokens=9_000,
    )  # fmt: skip
    line = usage_line(record, DayUsage(since=record.at, calls=4, requests=4, tokens=40_000), LIMITS)
    assert "in 8,100" in line and "out 1,200" in line and "total 9,300" in line
    assert "est. 9,000" in line
    assert "today 4/250 requests" in line


# ---- run and call checks ----


def today(requests: int = 0, tokens: int = 0) -> DayUsage:
    return DayUsage(since=at("2026-11-05T08:00:00"), calls=requests, requests=requests,
                    tokens=tokens)  # fmt: skip


def test_run_that_fits_is_ok() -> None:
    check = check_run(LIMITS, BUDGET, today(requests=50), planned_calls=100, tokens_per_call=10_000)
    assert check.verdict == "ok"
    assert check.requests_left == 200
    assert check.calls_that_fit == 160


def test_run_that_would_drain_the_day_warns() -> None:
    check = check_run(LIMITS, BUDGET, today(requests=50), planned_calls=180, tokens_per_call=10_000)
    assert check.verdict == "warn"
    assert "160 call(s) stay under that" in check.message


def test_run_that_cannot_finish_today_exceeds() -> None:
    check = check_run(LIMITS, BUDGET, today(requests=240), planned_calls=20, tokens_per_call=10_000)
    assert check.verdict == "exceeds"
    assert "at most 10 call(s) fit" in check.message


def test_daily_token_limit_is_used_when_the_model_has_one() -> None:
    limits = LlmLimits(rpm=10, tpm=250_000, rpd=1_000, tpd=1_000_000)
    check = check_run(
        limits, BUDGET, today(tokens=900_000), planned_calls=20, tokens_per_call=10_000
    )
    assert check.tokens_left == 100_000
    assert check.verdict == "exceeds"  # 10 calls fit by tokens


def test_call_too_large_for_the_minute_is_stopped() -> None:
    assert check_call(LIMITS, BUDGET, 100_000) is None
    reason = check_call(LIMITS, BUDGET, 130_000)
    assert reason is not None and "125,000" in reason


# ---- pacing ----


def test_pacer_spaces_calls_by_rpm() -> None:
    pacer = Pacer(LIMITS)
    start = at("2026-11-05T09:00:00")
    assert pacer.wait_seconds(start, 10_000) == 0
    pacer.record(start, 10_000)
    assert pacer.wait_seconds(start + timedelta(seconds=2), 10_000) == 4  # 60 s / 10 RPM


def test_pacer_waits_for_tokens_per_minute() -> None:
    pacer = Pacer(LlmLimits(rpm=100, tpm=20_000, rpd=250))
    start = at("2026-11-05T09:00:00")
    pacer.record(start, 15_000)
    # 15k used + 10k more > 20k: wait until the first call leaves the 60 s window.
    assert pacer.wait_seconds(start + timedelta(seconds=10), 10_000) == 50
    assert pacer.wait_seconds(start + timedelta(seconds=61), 10_000) == 0
