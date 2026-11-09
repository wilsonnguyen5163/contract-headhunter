"""Gemini client with the network mocked (httpx.MockTransport): nothing leaves the machine."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from conftest import repo_settings
from jobmatch.config import LlmSettings
from jobmatch.llm.gemini import GeminiProvider, build_body
from jobmatch.llm.provider import (
    DailyQuotaError,
    LlmConfigError,
    LlmRequest,
    LlmResponseError,
    LlmTimeoutError,
    RateLimitError,
)

KEY = "test-key-not-real"
REQUEST = LlmRequest(system="rules", user="<job>x</job>", response_schema={"type": "object"})


def llm_settings(**overrides: Any) -> LlmSettings:
    llm = repo_settings().llm
    assert llm is not None
    return llm.model_copy(update=overrides)


def answer(text: str = '{"ok": true}', finish: str = "STOP", **usage: int) -> dict[str, Any]:
    meta = {"promptTokenCount": 900, "candidatesTokenCount": 120, "totalTokenCount": 1020}
    meta.update(usage)
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "thinking…", "thought": True}, {"text": text}],
                },
                "finishReason": finish,
            }
        ],
        "usageMetadata": meta,
        "modelVersion": "gemini-test-001",
    }


def quota_error(quota_id: str, delay: str | None = None) -> dict[str, Any]:
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    return {"error": {"code": 429, "message": "Resource exhausted", "details": details}}


def provider(
    replies: list[tuple[int, Any]],
    seen: list[httpx.Request] | None = None,
    sleeps: list[float] | None = None,
    **overrides: Any,
) -> GeminiProvider:
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        status, body = queue.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    sleep: Callable[[float], None] = sleeps.append if sleeps is not None else lambda s: None
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiProvider(llm_settings(**overrides), KEY, client=client, sleep=sleep)


def test_request_shape_and_key_in_header_only() -> None:
    seen: list[httpx.Request] = []
    # Pinned here so the test does not depend on the live settings.yaml choice.
    result = provider(
        [(200, answer())], seen, thinking_budget=0, thinking_level=None, temperature=1, seed=7
    ).generate(REQUEST)
    sent = seen[0]
    assert sent.headers["x-goog-api-key"] == KEY
    assert KEY not in str(sent.url)
    assert str(sent.url).endswith(":generateContent")
    body = json.loads(sent.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "rules"
    assert body["contents"][0]["parts"][0]["text"] == "<job>x</job>"
    config = body["generationConfig"]
    assert config["temperature"] == 1
    assert config["seed"] == 7
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == {"type": "object"}
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert result.text == '{"ok": true}'  # the thought part is left out
    assert result.model == "gemini-test-001"


def test_thinking_level_instead_of_budget() -> None:
    body = build_body(llm_settings(thinking_budget=None, thinking_level="low"), REQUEST)
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    body = build_body(llm_settings(thinking_budget=None, thinking_level=None), REQUEST)
    assert "thinkingConfig" not in body["generationConfig"]


def test_no_seed_sent_when_none_is_set() -> None:
    body = build_body(llm_settings(seed=None), REQUEST)
    assert "seed" not in body["generationConfig"]


def test_usage_is_read_from_usage_metadata() -> None:
    result = provider([(200, answer(thoughtsTokenCount=40, totalTokenCount=1060))]).generate(
        REQUEST
    )
    assert result.usage.input_tokens == 900
    assert result.usage.output_tokens == 120
    assert result.usage.thinking_tokens == 40
    assert result.usage.total_tokens == 1060
    assert result.attempts == 1


def test_per_minute_429_is_retried_with_googles_delay() -> None:
    sleeps: list[float] = []
    replies = [
        (429, quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "7s")),
        (200, answer()),
    ]
    result = provider(replies, sleeps=sleeps).generate(REQUEST)
    assert sleeps == [7.0]
    assert result.attempts == 2


def test_per_minute_429_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    replies = [(429, quota_error("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))] * 3
    with pytest.raises(RateLimitError) as caught:
        provider(replies, sleeps=sleeps, max_retries=2, retry_base_seconds=5).generate(REQUEST)
    assert sleeps == [5, 10]  # backoff doubles when Google gives no delay
    assert caught.value.attempts == 3


def test_daily_429_is_never_retried() -> None:
    sleeps: list[float] = []
    replies = [(429, quota_error("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))]
    with pytest.raises(DailyQuotaError) as caught:
        provider(replies, sleeps=sleeps).generate(REQUEST)
    assert sleeps == []
    assert caught.value.attempts == 1


def test_server_error_is_retried_then_succeeds() -> None:
    sleeps: list[float] = []
    replies = [(503, {"error": {"message": "overloaded"}}), (200, answer())]
    assert provider(replies, sleeps=sleeps).generate(REQUEST).attempts == 2
    assert len(sleeps) == 1


def test_network_error_is_retried() -> None:
    replies: list[tuple[int, Any]] = [(0, httpx.ConnectError("down")), (200, answer())]
    assert provider(replies).generate(REQUEST).attempts == 2


def test_a_timeout_is_not_retried() -> None:
    """User decision 2026-11-08: a timeout moves on to the next model at once (each retry
    would wait the full timeout again)."""
    replies: list[tuple[int, Any]] = [(0, httpx.ReadTimeout("slow")), (200, answer())]
    sleeps: list[float] = []
    with pytest.raises(LlmTimeoutError) as caught:
        provider(replies, sleeps=sleeps).generate(REQUEST)
    assert caught.value.attempts == 1 and sleeps == []
    assert "no answer within" in str(caught.value)


def test_with_model_keeps_the_key_and_settings_for_another_model() -> None:
    first = provider([(200, answer())])
    other = first.with_model("gemini-other")
    assert other.model == "gemini-other" and other.url.endswith(
        "/models/gemini-other:generateContent"
    )
    assert other.settings.temperature == first.settings.temperature


def test_bad_request_fails_at_once_without_the_key() -> None:
    replies = [(400, {"error": {"message": "Invalid JSON payload"}})]
    with pytest.raises(LlmResponseError) as caught:
        provider(replies).generate(REQUEST)
    assert "Invalid JSON payload" in str(caught.value)
    assert KEY not in str(caught.value)


def test_cut_off_answer_fails_but_keeps_its_usage() -> None:
    with pytest.raises(LlmResponseError) as caught:
        provider([(200, answer(finish="MAX_TOKENS"))]).generate(REQUEST)
    assert "max_output_tokens" in str(caught.value)
    assert caught.value.usage is not None and caught.value.usage.total_tokens == 1020


def test_blocked_prompt_fails() -> None:
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {}}
    with pytest.raises(LlmResponseError, match="SAFETY"):
        provider([(200, blocked)]).generate(REQUEST)


def test_missing_key_stops_before_any_request() -> None:
    with pytest.raises(LlmConfigError, match="GEMINI_API_KEY"):
        GeminiProvider.from_settings(llm_settings(), env={})
