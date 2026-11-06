"""The LLM provider interface. The rest of v1 only sees these models and errors, so another
free provider could be added later without touching the matching code."""

from typing import Any, Protocol

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token counts reported by the API for one call (all attempts' final answer)."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    thinking_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class LlmRequest(BaseModel):
    system: str  # fixed instructions (trusted)
    user: str  # the job and contractor data (untrusted posting text inside)
    response_schema: dict[str, Any]  # JSON schema the answer must follow


class LlmResponse(BaseModel):
    text: str  # the JSON answer, not yet validated
    usage: TokenUsage
    model: str
    finish_reason: str | None = None
    http_status: int
    attempts: int = Field(ge=1)


class LlmError(Exception):
    """A call that produced no usable answer. `usage` is set when tokens were still spent,
    so the usage log stays honest; `attempts` counts requests sent."""

    def __init__(
        self,
        message: str,
        *,
        usage: TokenUsage | None = None,
        http_status: int | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.http_status = http_status
        self.attempts = attempts


class LlmConfigError(LlmError):
    """Missing key or settings: nothing was sent."""


class RateLimitError(LlmError):
    """Per-minute limit still hit after every retry."""


class DailyQuotaError(LlmError):
    """Per-day limit reached: no call will succeed until midnight Pacific time."""


class LlmResponseError(LlmError):
    """HTTP error, blocked prompt, cut-off or empty answer."""


class LLMProvider(Protocol):
    model: str

    def generate(self, request: LlmRequest) -> LlmResponse: ...
