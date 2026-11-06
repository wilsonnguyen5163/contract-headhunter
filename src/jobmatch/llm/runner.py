"""Send one call with every guard around it: the size check before, the usage log after
(including failed calls, which may still have spent tokens). Used by every command that
calls the API, so no call can skip the budget guard or the usage report."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from jobmatch.config import LlmSettings
from jobmatch.llm.provider import (
    DailyQuotaError,
    LlmError,
    LLMProvider,
    LlmRequest,
    LlmResponse,
    RateLimitError,
)
from jobmatch.llm.usage import UsageLedger, check_call, new_call_id, usage_line
from jobmatch.schemas import LlmCallStatus, LlmUsage

_NOTE_MAX = 300


class CallOutcome(BaseModel):
    response: LlmResponse | None
    usage: LlmUsage
    line: str  # the usage line printed for this call

    @property
    def ok(self) -> bool:
        return self.response is not None


def send(
    provider: LLMProvider,
    ledger: UsageLedger,
    settings: LlmSettings,
    request: LlmRequest,
    *,
    purpose: str,
    job_ids: Sequence[str] = (),
    estimated_tokens: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CallOutcome:
    """Never raises for API problems: the outcome says what happened and the log has it."""
    base = {
        "call_id": new_call_id(),
        "at": clock(),
        "model": settings.model,
        "purpose": purpose,
        "job_ids": list(job_ids),
        "estimated_tokens": estimated_tokens,
    }
    response: LlmResponse | None = None
    if too_big := check_call(settings.limits, settings.budget, estimated_tokens):
        usage = LlmUsage(**base, status=LlmCallStatus.NOT_SENT, attempts=0, note=too_big)
    else:
        try:
            response = provider.generate(request)
            u = response.usage
            usage = LlmUsage(
                **base,
                status=LlmCallStatus.OK,
                http_status=response.http_status,
                attempts=response.attempts,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                thinking_tokens=u.thinking_tokens,
                total_tokens=u.total_tokens,
            )
        except LlmError as exc:
            status = (
                LlmCallStatus.DAILY_QUOTA
                if isinstance(exc, DailyQuotaError)
                else LlmCallStatus.RATE_LIMITED
                if isinstance(exc, RateLimitError)
                else LlmCallStatus.ERROR
            )
            u = exc.usage
            usage = LlmUsage(
                **base,
                status=status,
                http_status=exc.http_status,
                attempts=exc.attempts,
                input_tokens=u.input_tokens if u else 0,
                output_tokens=u.output_tokens if u else 0,
                thinking_tokens=u.thinking_tokens if u else 0,
                total_tokens=u.total_tokens if u else 0,
                note=str(exc)[:_NOTE_MAX],
            )
    ledger.record(usage)
    line = usage_line(usage, ledger.today(clock()), settings.limits)
    return CallOutcome(response=response, usage=usage, line=line)
