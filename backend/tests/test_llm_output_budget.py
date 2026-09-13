"""plan_8_1 section 1.1: an output budget a caller can set, and what each call cost.

Every provider client takes an optional ``max_tokens``. Omitted, each behaves exactly as before: the
Anthropic client sends 4096 and the OpenAI and Google clients send no cap. Given, the budget reaches the
provider, and a stop at that budget is reported as a truncation carrying the text that arrived.

Every call reports its output tokens and its stop reason, in the log and on the answer it returns, so a
read can record what it used.
"""

import json
import logging

import httpx
import pytest

from app.services.llm_provider_clients import ProviderError, anthropic_client, google_client, openai_client


class _Recorder:
    """A fake httpx transport: hands back a scripted response per call and keeps each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(seconds):
        return None

    monkeypatch.setattr("asyncio.sleep", _sleep)


def _patch_transport(monkeypatch, module, recorder):
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", _factory)


def _sse(*events: tuple[str, dict]) -> httpx.Response:
    text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})


def _anthropic_stream(text_parts: list[str], *, stop_reason: str = "end_turn", output_tokens: int = 12):
    events: list[tuple[str, dict]] = [
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 900, "output_tokens": 1}}}),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        ("ping", {"type": "ping"}),
    ]
    for part in text_parts:
        events.append(
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": part}},
            )
        )
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": output_tokens}},
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return _sse(*events)


_ANTHROPIC_OK = {
    "content": [{"type": "text", "text": "hello"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 40, "output_tokens": 7},
}
_OPENAI_OK = {
    "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 40, "completion_tokens": 7},
}
_GOOGLE_OK = {
    "candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 7},
}


class TestAnOmittedBudgetBehavesAsBefore:
    @pytest.mark.asyncio
    async def test_anthropic_sends_4096_and_does_not_stream(self, monkeypatch):
        rec = _Recorder(httpx.Response(200, json=_ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        text = await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k")
        assert text == "hello"
        body = rec.body()
        assert body["max_tokens"] == 4096
        assert "stream" not in body

    @pytest.mark.asyncio
    async def test_openai_sends_no_cap(self, monkeypatch):
        rec = _Recorder(httpx.Response(200, json=_OPENAI_OK))
        _patch_transport(monkeypatch, openai_client, rec)
        await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="k")
        body = rec.body()
        assert "max_tokens" not in body and "max_completion_tokens" not in body

    @pytest.mark.asyncio
    async def test_google_sends_no_cap(self, monkeypatch):
        rec = _Recorder(httpx.Response(200, json=_GOOGLE_OK))
        _patch_transport(monkeypatch, google_client, rec)
        await google_client.submit(prompt="p", payload="x", model="gemini-x", api_key="k")
        assert "maxOutputTokens" not in json.dumps(rec.body())

    @pytest.mark.asyncio
    async def test_openai_without_a_budget_still_returns_a_length_capped_answer(self, monkeypatch):
        """Only a caller that set a budget is told its answer stopped at it; nothing else changes."""
        capped = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
        rec = _Recorder(httpx.Response(200, json=capped))
        _patch_transport(monkeypatch, openai_client, rec)
        assert await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="k") == "partial"


class TestAGivenBudgetReachesTheProvider:
    @pytest.mark.asyncio
    async def test_anthropic_sends_the_budget_and_streams_the_answer(self, monkeypatch):
        rec = _Recorder(_anthropic_stream(["hel", "lo"], output_tokens=2))
        _patch_transport(monkeypatch, anthropic_client, rec)
        text = await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=16000)
        assert text == "hello"
        body = rec.body()
        assert body["max_tokens"] == 16000
        assert body["stream"] is True

    @pytest.mark.asyncio
    async def test_openai_sends_the_budget(self, monkeypatch):
        rec = _Recorder(httpx.Response(200, json=_OPENAI_OK))
        _patch_transport(monkeypatch, openai_client, rec)
        await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="k", max_tokens=16000)
        assert rec.body()["max_completion_tokens"] == 16000

    @pytest.mark.asyncio
    async def test_google_sends_the_budget(self, monkeypatch):
        rec = _Recorder(httpx.Response(200, json=_GOOGLE_OK))
        _patch_transport(monkeypatch, google_client, rec)
        await google_client.submit(prompt="p", payload="x", model="gemini-x", api_key="k", max_tokens=16000)
        assert rec.body()["generationConfig"]["maxOutputTokens"] == 16000


class TestEveryAnswerSaysWhatItCost:
    @pytest.mark.asyncio
    async def test_anthropic_plain_answer(self, monkeypatch, caplog):
        rec = _Recorder(httpx.Response(200, json=_ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with caplog.at_level(logging.INFO, logger="bioaf.llm.anthropic"):
            text = await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k")
        assert (text.output_tokens, text.stop_reason) == (7, "end_turn")
        assert "output_tokens=7" in caplog.text and "stop_reason=end_turn" in caplog.text

    @pytest.mark.asyncio
    async def test_anthropic_streamed_answer(self, monkeypatch, caplog):
        rec = _Recorder(_anthropic_stream(["hi"], output_tokens=321))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with caplog.at_level(logging.INFO, logger="bioaf.llm.anthropic"):
            text = await anthropic_client.submit(
                prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=9000
            )
        assert (text.output_tokens, text.stop_reason) == (321, "end_turn")
        assert "output_tokens=321" in caplog.text

    @pytest.mark.asyncio
    async def test_openai_answer(self, monkeypatch, caplog):
        rec = _Recorder(httpx.Response(200, json=_OPENAI_OK))
        _patch_transport(monkeypatch, openai_client, rec)
        with caplog.at_level(logging.INFO, logger="bioaf.llm.openai"):
            text = await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="k")
        assert (text.output_tokens, text.stop_reason) == (7, "stop")
        assert "output_tokens=7" in caplog.text and "stop_reason=stop" in caplog.text

    @pytest.mark.asyncio
    async def test_google_answer(self, monkeypatch, caplog):
        rec = _Recorder(httpx.Response(200, json=_GOOGLE_OK))
        _patch_transport(monkeypatch, google_client, rec)
        with caplog.at_level(logging.INFO, logger="bioaf.llm.google"):
            text = await google_client.submit(prompt="p", payload="x", model="gemini-x", api_key="k")
        assert (text.output_tokens, text.stop_reason) == (7, "STOP")
        assert "output_tokens=7" in caplog.text


class TestAStopAtTheBudgetIsATruncationWithItsText:
    @pytest.mark.asyncio
    async def test_anthropic_stream_that_stops_at_the_budget(self, monkeypatch):
        rec = _Recorder(_anthropic_stream(['{"claims": [', '{"claim'], stop_reason="max_tokens", output_tokens=16000))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as exc:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=16000)
        assert exc.value.error_class == "truncated"
        assert exc.value.text == '{"claims": [{"claim'
        assert exc.value.output_tokens == 16000
        assert exc.value.stop_reason == "max_tokens"

    @pytest.mark.asyncio
    async def test_anthropic_plain_answer_that_stops_at_the_cap_keeps_its_text(self, monkeypatch):
        capped = {
            "content": [{"type": "text", "text": '{"a": '}],
            "stop_reason": "max_tokens",
            "usage": {"output_tokens": 4096},
        }
        rec = _Recorder(httpx.Response(200, json=capped))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as exc:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k")
        assert (exc.value.error_class, exc.value.text, exc.value.output_tokens) == ("truncated", '{"a": ', 4096)

    @pytest.mark.asyncio
    async def test_openai_with_a_budget(self, monkeypatch):
        capped = {
            "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 16000},
        }
        rec = _Recorder(httpx.Response(200, json=capped))
        _patch_transport(monkeypatch, openai_client, rec)
        with pytest.raises(ProviderError) as exc:
            await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="k", max_tokens=16000)
        assert (exc.value.error_class, exc.value.text, exc.value.output_tokens) == ("truncated", "partial", 16000)

    @pytest.mark.asyncio
    async def test_google_with_a_budget(self, monkeypatch):
        capped = {
            "candidates": [{"content": {"parts": [{"text": "partial"}]}, "finishReason": "MAX_TOKENS"}],
            "usageMetadata": {"candidatesTokenCount": 16000},
        }
        rec = _Recorder(httpx.Response(200, json=capped))
        _patch_transport(monkeypatch, google_client, rec)
        with pytest.raises(ProviderError) as exc:
            await google_client.submit(prompt="p", payload="x", model="gemini-x", api_key="k", max_tokens=16000)
        assert (exc.value.error_class, exc.value.text) == ("truncated", "partial")


class TestTheStreamedCall:
    @pytest.mark.asyncio
    async def test_a_refusal_in_the_stream_is_a_refusal(self, monkeypatch):
        rec = _Recorder(_anthropic_stream(["I can't help with that."], stop_reason="refusal"))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as exc:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=16000)
        assert exc.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_rate_limit_before_the_stream_is_retried(self, monkeypatch, no_sleep):
        rec = _Recorder(httpx.Response(429, json={"error": "slow down"}), _anthropic_stream(["ok"]))
        _patch_transport(monkeypatch, anthropic_client, rec)
        text = await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=16000)
        assert text == "ok"
        assert len(rec.requests) == 2

    @pytest.mark.asyncio
    async def test_an_auth_failure_is_not_retried(self, monkeypatch, no_sleep):
        rec = _Recorder(httpx.Response(401, json={"error": "bad key"}))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as exc:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=16000)
        assert exc.value.error_class == "auth"
        assert len(rec.requests) == 1

    @pytest.mark.asyncio
    async def test_an_error_event_mid_stream_is_a_server_failure_retried_within_the_transport_policy(
        self, monkeypatch, no_sleep
    ):
        overloaded = _sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"output_tokens": 1}}}),
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        )
        rec = _Recorder(overloaded, _anthropic_stream(["ok"]))
        _patch_transport(monkeypatch, anthropic_client, rec)
        assert (
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=9000)
            == "ok"
        )
        assert len(rec.requests) == 2

    @pytest.mark.asyncio
    async def test_a_stream_past_its_deadline_is_a_timeout_not_a_truncation(self, monkeypatch):
        rec = _Recorder(_anthropic_stream(["slow"]))
        _patch_transport(monkeypatch, anthropic_client, rec)
        monkeypatch.setattr(anthropic_client, "STREAM_DEADLINE_SECONDS", 0.0)
        with pytest.raises(ProviderError) as exc:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="k", max_tokens=9000)
        assert exc.value.error_class == "timeout"
