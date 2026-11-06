"""Gemini REST client (generateContent), over httpx. The only v1 module that reaches the
network, and only generativelanguage.googleapis.com (CLAUDE.md rule 4).

- The key goes in the x-goog-api-key header, never in the URL, logs or errors.
- temperature, thinking and the answer's JSON schema come from settings and the request.
- Per-minute 429s, 5xx and network errors are retried with backoff (Google's RetryInfo delay
  when given). A per-day 429 is never retried: DailyQuotaError.
- Token counts come from the answer's usageMetadata, and are kept on errors too.
"""

import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from jobmatch.config import LlmSettings
from jobmatch.llm.provider import (
    DailyQuotaError,
    LlmConfigError,
    LlmError,
    LlmRequest,
    LlmResponse,
    LlmResponseError,
    RateLimitError,
    TokenUsage,
)

_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
_RETRYABLE_STATUS = {500, 502, 503, 504}


def build_body(settings: LlmSettings, request: LlmRequest) -> dict[str, Any]:
    """The exact JSON body sent to generateContent (the key is not part of it)."""
    generation: dict[str, Any] = {
        "temperature": settings.temperature,
        "maxOutputTokens": settings.max_output_tokens,
        "responseMimeType": "application/json",
        "responseJsonSchema": request.response_schema,
    }
    thinking: dict[str, Any] = {}
    if settings.thinking_budget is not None:
        thinking["thinkingBudget"] = settings.thinking_budget
    if settings.thinking_level is not None:
        thinking["thinkingLevel"] = settings.thinking_level
    if thinking:
        generation["thinkingConfig"] = thinking
    return {
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": [{"role": "user", "parts": [{"text": request.user}]}],
        "generationConfig": generation,
    }


def parse_usage(payload: Mapping[str, Any]) -> TokenUsage:
    meta = payload.get("usageMetadata") or {}
    return TokenUsage(
        input_tokens=int(meta.get("promptTokenCount", 0)),
        output_tokens=int(meta.get("candidatesTokenCount", 0)),
        thinking_tokens=int(meta.get("thoughtsTokenCount", 0)),
        cached_tokens=int(meta.get("cachedContentTokenCount", 0)),
        total_tokens=int(meta.get("totalTokenCount", 0)),
    )


def _error_details(payload: Any) -> tuple[str, list[str], float | None]:
    """(message, quota ids, retry delay in seconds) from a Google error body."""
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = str(error.get("message", "")) if isinstance(error, dict) else ""
    quota_ids: list[str] = []
    delay: float | None = None
    for detail in error.get("details", []) if isinstance(error, dict) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            quota_ids += [str(v.get("quotaId", "")) for v in detail.get("violations", [])]
        elif kind.endswith("RetryInfo"):
            match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
            delay = float(match.group(1)) if match else None
    return message, quota_ids, delay


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class GeminiProvider:
    def __init__(
        self,
        settings: LlmSettings,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LlmConfigError(f"no API key: set {settings.api_key_env} in .env")
        self.settings = settings
        self.model = settings.model
        self._key = api_key
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)
        self._sleep = sleep

    @classmethod
    def from_settings(
        cls,
        settings: LlmSettings,
        *,
        env: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "GeminiProvider":
        env = os.environ if env is None else env
        return cls(settings, env.get(settings.api_key_env, ""), client=client, sleep=sleep)

    @property
    def url(self) -> str:
        return f"{self.settings.base_url.rstrip('/')}/models/{self.model}:generateContent"

    def _backoff(self, attempt: int, hint: float | None) -> float:
        return hint if hint is not None else self.settings.retry_base_seconds * 2 ** (attempt - 1)

    def generate(self, request: LlmRequest) -> LlmResponse:
        body = build_body(self.settings, request)
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        tries = self.settings.max_retries + 1
        last: LlmError | None = None
        for attempt in range(1, tries + 1):
            try:
                response = self._client.post(
                    self.url, json=body, headers=headers, timeout=self.settings.timeout_seconds
                )
            except httpx.TransportError as exc:
                # The exception text names the host only; the key is in a header.
                last = LlmResponseError(f"network error: {type(exc).__name__}", attempts=attempt)
                if attempt < tries:
                    self._sleep(self._backoff(attempt, None))
                    continue
                raise last from exc

            status = response.status_code
            payload = _json(response)
            if status == 429:
                message, quota_ids, delay = _error_details(payload)
                if any("PerDay" in q for q in quota_ids):
                    raise DailyQuotaError(
                        f"daily limit reached ({', '.join(quota_ids)})",
                        http_status=status,
                        attempts=attempt,
                    )
                last = RateLimitError(
                    f"per-minute limit: {message or 'HTTP 429'}", http_status=status,
                    attempts=attempt,
                )  # fmt: skip
                if attempt < tries:
                    self._sleep(self._backoff(attempt, delay))
                    continue
                raise last
            if status in _RETRYABLE_STATUS:
                message, _, delay = _error_details(payload)
                last = LlmResponseError(
                    f"HTTP {status}: {message}".strip(), http_status=status, attempts=attempt
                )
                if attempt < tries:
                    self._sleep(self._backoff(attempt, delay))
                    continue
                raise last
            if status != 200 or not isinstance(payload, dict):
                message, _, _ = _error_details(payload)
                raise LlmResponseError(
                    f"HTTP {status}: {message or 'unexpected answer'}",
                    http_status=status,
                    attempts=attempt,
                )
            return self._answer(payload, status, attempt)
        raise last or LlmResponseError("no attempt made")  # unreachable: tries >= 1

    def _answer(self, payload: dict[str, Any], status: int, attempts: int) -> LlmResponse:
        usage = parse_usage(payload)

        def fail(message: str) -> LlmResponseError:
            return LlmResponseError(message, usage=usage, http_status=status, attempts=attempts)

        candidates = payload.get("candidates") or []
        if not candidates:
            reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise fail(f"no answer: {reason}")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if finish == "MAX_TOKENS":
            raise fail("answer cut at llm.max_output_tokens")
        if finish not in (None, "STOP"):
            raise fail(f"answer stopped: {finish}")
        if not text.strip():
            raise fail("empty answer")
        return LlmResponse(
            text=text,
            usage=usage,
            model=str(payload.get("modelVersion") or self.model),
            finish_reason=finish,
            http_status=status,
            attempts=attempts,
        )
