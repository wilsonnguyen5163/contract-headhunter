"""Token usage per call and the daily budget guard (CLAUDE.md v1 rule: report token usage for
every call, and flag calls that would use up the day's allowance).

- Every call, sent or stopped, is appended to data/llm_usage.csv (counts and IDs only).
- Google's daily limits reset at midnight Pacific time, so "today" starts then.
- Requests are counted conservatively: every attempt (retries included) counts.
- check_run: before a run, compares its estimate with what is left today.
- check_call: before each call, refuses one estimated too large for the per-minute limit.
- Pacer: waits between calls to stay under requests and tokens per minute.
"""

import math
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel

from jobmatch.config import LlmBudget, LlmLimits
from jobmatch.schemas import LlmUsage
from jobmatch.storage.repositories import UsageRepository

# ---- the Pacific-time day (US daylight saving rules, no tz database needed) ----


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    first_sunday = first + timedelta(days=(6 - first.weekday()) % 7)
    return first_sunday + timedelta(weeks=n - 1)


def pacific_offset(at: datetime) -> timedelta:
    """UTC offset of US Pacific time at `at`: -7 h from the second Sunday of March 2:00 to
    the first Sunday of November 2:00 (local), else -8 h."""
    at = at.astimezone(UTC)
    starts = datetime.combine(_nth_sunday(at.year, 3, 2), time(10), UTC)  # 2:00 PST
    ends = datetime.combine(_nth_sunday(at.year, 11, 1), time(9), UTC)  # 2:00 PDT
    return timedelta(hours=-7) if starts <= at < ends else timedelta(hours=-8)


def pacific_day_start(now: datetime) -> datetime:
    """The last midnight Pacific time before `now`, in UTC."""
    now = now.astimezone(UTC)
    local = (now + pacific_offset(now)).replace(tzinfo=None)
    midnight = datetime.combine(local.date(), time(0))
    guess = (midnight - pacific_offset(now)).replace(tzinfo=UTC)
    # Daylight saving changes at 2:00, so midnight may have had the other offset.
    return (midnight - pacific_offset(guess)).replace(tzinfo=UTC)


# ---- the log ----


def new_call_id() -> str:
    return uuid.uuid4().hex[:16]


class DayUsage(BaseModel):
    since: datetime
    calls: int
    requests: int
    tokens: int


def day_usage(records: Iterable[LlmUsage], now: datetime) -> DayUsage:
    since = pacific_day_start(now)
    today = [r for r in records if r.at >= since]
    return DayUsage(
        since=since,
        calls=len(today),
        requests=sum(r.attempts for r in today),
        tokens=sum(r.total_tokens for r in today),
    )


class UsageLedger:
    """The usage log (loader.llm_usage: data/llm_usage.csv with the CSV backend)."""

    def __init__(self, usage: UsageRepository) -> None:
        self._usage = usage

    def record(self, usage: LlmUsage) -> None:
        self._usage.record(usage)

    def all(self) -> list[LlmUsage]:
        return self._usage.list_all()

    def today(self, now: datetime | None = None) -> DayUsage:
        return day_usage(self.all(), now or datetime.now(UTC))


def usage_line(usage: LlmUsage, today: DayUsage, limits: LlmLimits) -> str:
    """One terminal line per call: what it cost and where today stands."""
    jobs = ",".join(usage.job_ids) or "-"
    day_tokens = f"{today.tokens:,}" + (f"/{limits.tpd:,}" if limits.tpd else "")
    return (
        f"[llm] {usage.purpose} {jobs} · {usage.status.value} · "
        f"in {usage.input_tokens:,} · out {usage.output_tokens:,} · "
        f"think {usage.thinking_tokens:,} · total {usage.total_tokens:,}"
        + (f" (est. {usage.estimated_tokens:,})" if usage.estimated_tokens is not None else "")
        + f" · today {today.requests}/{limits.rpd} requests, {day_tokens} tokens"
    )


# ---- checks ----


class RunCheck(BaseModel):
    verdict: Literal["ok", "warn", "exceeds"]
    planned_calls: int
    tokens_per_call: int
    requests_left: int
    tokens_left: int | None  # None: the model has no daily token limit
    calls_that_fit: int  # calls within drain_warn_share of what is left
    message: str


def check_run(
    limits: LlmLimits,
    budget: LlmBudget,
    today: DayUsage,
    planned_calls: int,
    tokens_per_call: int,
) -> RunCheck:
    """ok: the run fits comfortably. warn: it would use more than drain_warn_share of what is
    left today. exceeds: it cannot finish today at all."""
    requests_left = max(limits.rpd - today.requests, 0)
    tokens_left = max(limits.tpd - today.tokens, 0) if limits.tpd else None
    by_requests = requests_left
    by_tokens = tokens_left // max(tokens_per_call, 1) if tokens_left is not None else by_requests
    can_run = min(by_requests, by_tokens)
    calls_that_fit = math.floor(can_run * budget.drain_warn_share)
    if planned_calls > can_run:
        verdict: Literal["ok", "warn", "exceeds"] = "exceeds"
    elif planned_calls > calls_that_fit:
        verdict = "warn"
    else:
        verdict = "ok"
    left = f"{requests_left} request(s)" + (
        f" and {tokens_left:,} token(s)" if tokens_left is not None else ""
    )
    message = (
        f"{planned_calls} call(s) of about {tokens_per_call:,} tokens planned; "
        f"{left} left today (limits {limits.rpd} RPD"
        + (f", {limits.tpd:,} TPD" if limits.tpd else "")
        + ")."
    )
    if verdict == "exceeds":
        message += f" The run cannot finish today: at most {can_run} call(s) fit."
    elif verdict == "warn":
        share = round(budget.drain_warn_share * 100)
        message += (
            f" It would use more than {share}% of what is left today; "
            f"{calls_that_fit} call(s) stay under that."
        )
    return RunCheck(
        verdict=verdict,
        planned_calls=planned_calls,
        tokens_per_call=tokens_per_call,
        requests_left=requests_left,
        tokens_left=tokens_left,
        calls_that_fit=calls_that_fit,
        message=message,
    )


def check_call(limits: LlmLimits, budget: LlmBudget, estimated_tokens: int) -> str | None:
    """A reason to stop this one call before sending it, or None."""
    cap = math.floor(limits.tpm * budget.call_max_tpm_share)
    if estimated_tokens > cap:
        return (
            f"estimated {estimated_tokens:,} tokens is above {cap:,} "
            f"({round(budget.call_max_tpm_share * 100)}% of the {limits.tpm:,} per-minute limit)"
        )
    return None


class Pacer:
    """How long to wait before the next call to stay under RPM and TPM."""

    WINDOW = timedelta(seconds=60)

    def __init__(self, limits: LlmLimits) -> None:
        self._limits = limits
        self._calls: list[tuple[datetime, int]] = []  # (sent at, tokens)

    def record(self, at: datetime, tokens: int) -> None:
        self._calls.append((at, tokens))

    def wait_seconds(self, now: datetime, estimated_tokens: int) -> float:
        recent = [(t, n) for t, n in self._calls if now - t < self.WINDOW]
        self._calls = recent
        waits = [0.0]
        if recent:
            spacing = timedelta(seconds=60 / self._limits.rpm)
            waits.append((recent[-1][0] + spacing - now).total_seconds())
        if len(recent) >= self._limits.rpm:
            waits.append((recent[-self._limits.rpm][0] + self.WINDOW - now).total_seconds())
        used = sum(n for _, n in recent)
        if recent and used + estimated_tokens > self._limits.tpm:
            # Wait until enough of the window has expired to make room.
            room_needed = used + estimated_tokens - self._limits.tpm
            freed = 0
            for t, n in recent:
                freed += n
                if freed >= room_needed:
                    waits.append((t + self.WINDOW - now).total_seconds())
                    break
        return max(waits)
