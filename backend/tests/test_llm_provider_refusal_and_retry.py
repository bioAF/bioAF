"""plan_7 step 14b: a refusal is a fact, and a 429 is worth one more try.

**A refusal was not detectable at any layer.** Some models decline biotech queries outright, and no
provider client read the field that says so:

| Provider | On a safety block | What the caller saw |
|---|---|---|
| Anthropic | 200, `stop_reason: "refusal"`, refusal prose as a text block | prose, no fence, empty dict |
| OpenAI | 200, `finish_reason: "content_filter"`, content often null | `None` content, silently empty |
| Google | 200, `candidates[0]` carrying no `content` key | a `KeyError` reported as `error_class="parse"`, which is a lie |

`llm_decision` cannot classify what the transport threw away, which is why this sits below it.

**Transport retry lives here too**, for the same reason: every caller gets rate-limit resilience
without writing a loop, and the decision helper stays free of any retry concept.
"""

import httpx
import pytest

from app.services.llm_provider_clients import ProviderError, anthropic_client, google_client, openai_client


class _Recorder:
    """A fake httpx transport: hands back a scripted response per call and counts the calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        r = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


@pytest.fixture
def no_sleep(monkeypatch):
    """Retry backoff, without the wall clock."""
    slept: list[float] = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", _sleep)
    return slept


def _patch_transport(monkeypatch, module, recorder):
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", _factory)


_ANTHROPIC_OK = {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}
_OPENAI_OK = {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}
_GOOGLE_OK = {"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}]}


class TestAnthropicRefusal:
    @pytest.mark.asyncio
    async def test_a_refusal_stop_reason_is_a_refusal(self, monkeypatch, no_sleep):
        """The field was there all along and nothing read it."""
        rec = _Recorder(
            _json(
                200,
                {
                    "content": [{"type": "text", "text": "I can't help with that."}],
                    "stop_reason": "refusal",
                },
            )
        )
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as e:
            await anthropic_client.submit("s", "p", "claude-opus-4-8", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_normal_answer_is_untouched(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(200, _ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        assert await anthropic_client.submit("s", "p", "m", "k") == "hello"

    @pytest.mark.asyncio
    async def test_a_200_with_no_text_at_all_is_a_refusal_not_an_empty_answer(self, monkeypatch, no_sleep):
        """An empty string reaches `llm_decision` as `unparseable`, which is a true statement about
        the answer and a false one about what happened."""
        rec = _Recorder(_json(200, {"content": [], "stop_reason": "end_turn"}))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as e:
            await anthropic_client.submit("s", "p", "m", "k")
        assert e.value.error_class == "refusal"


class TestOpenAiRefusal:
    @pytest.mark.asyncio
    async def test_content_filter_is_a_refusal(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(200, {"choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]}))
        _patch_transport(monkeypatch, openai_client, rec)
        with pytest.raises(ProviderError) as e:
            await openai_client.submit("s", "p", "gpt-5", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_an_explicit_refusal_field_is_a_refusal(self, monkeypatch, no_sleep):
        rec = _Recorder(
            _json(
                200,
                {"choices": [{"message": {"content": None, "refusal": "I won't do that"}, "finish_reason": "stop"}]},
            )
        )
        _patch_transport(monkeypatch, openai_client, rec)
        with pytest.raises(ProviderError) as e:
            await openai_client.submit("s", "p", "gpt-5", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_null_content_is_a_refusal_rather_than_the_string_none(self, monkeypatch, no_sleep):
        """`data["choices"][0]["message"]["content"]` returned None and every caller then ran a regex
        over it. Silently empty."""
        rec = _Recorder(_json(200, {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}))
        _patch_transport(monkeypatch, openai_client, rec)
        with pytest.raises(ProviderError) as e:
            await openai_client.submit("s", "p", "gpt-5", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_normal_answer_is_untouched(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(200, _OPENAI_OK))
        _patch_transport(monkeypatch, openai_client, rec)
        assert await openai_client.submit("s", "p", "m", "k") == "hello"


class TestGoogleRefusal:
    @pytest.mark.asyncio
    async def test_a_prompt_block_reason_is_a_refusal(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
        _patch_transport(monkeypatch, google_client, rec)
        with pytest.raises(ProviderError) as e:
            await google_client.submit("s", "p", "gemini-2.5-pro", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_candidate_with_no_content_is_a_refusal_not_a_parse_failure(self, monkeypatch, no_sleep):
        """It used to raise KeyError and be reported as `error_class="parse"`, which told the user
        bioAF could not read the answer when in fact the model declined to give one."""
        rec = _Recorder(_json(200, {"candidates": [{"finishReason": "SAFETY"}]}))
        _patch_transport(monkeypatch, google_client, rec)
        with pytest.raises(ProviderError) as e:
            await google_client.submit("s", "p", "m", "k")
        assert e.value.error_class == "refusal"

    @pytest.mark.asyncio
    async def test_a_normal_answer_is_untouched(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(200, _GOOGLE_OK))
        _patch_transport(monkeypatch, google_client, rec)
        assert await google_client.submit("s", "p", "m", "k") == "hello"


class TestTransportRetry:
    """429 and 5xx are worth one more try. Every other status is an answer."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_a_retryable_status_is_retried_and_can_succeed(self, monkeypatch, no_sleep, status):
        rec = _Recorder(_json(status, {"error": "later"}), _json(200, _ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        assert await anthropic_client.submit("s", "p", "m", "k") == "hello"
        assert rec.calls == 2

    @pytest.mark.asyncio
    async def test_it_gives_up_and_reports_the_class_it_always_did(self, monkeypatch, no_sleep):
        """A retry that never succeeds must not change what the caller is told."""
        rec = _Recorder(_json(429, {"error": "slow down"}))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as e:
            await anthropic_client.submit("s", "p", "m", "k")
        assert e.value.error_class == "rate_limit"

    @pytest.mark.asyncio
    async def test_it_backs_off_between_attempts(self, monkeypatch, no_sleep):
        rec = _Recorder(_json(503, {"error": "x"}), _json(503, {"error": "x"}), _json(200, _ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        await anthropic_client.submit("s", "p", "m", "k")
        assert no_sleep == sorted(no_sleep)  # increasing, not a hot loop
        assert all(s > 0 for s in no_sleep)

    @pytest.mark.asyncio
    async def test_an_auth_failure_is_not_retried(self, monkeypatch, no_sleep):
        """A bad key will be just as bad in two seconds, and retrying it wastes the study's time."""
        rec = _Recorder(_json(401, {"error": "bad key"}))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError) as e:
            await anthropic_client.submit("s", "p", "m", "k")
        assert e.value.error_class == "auth"
        assert rec.calls == 1

    @pytest.mark.asyncio
    async def test_a_refusal_is_not_retried(self, monkeypatch, no_sleep):
        """Asking a model the same question again after it declined is not resilience."""
        rec = _Recorder(_json(200, {"content": [], "stop_reason": "refusal"}))
        _patch_transport(monkeypatch, anthropic_client, rec)
        with pytest.raises(ProviderError):
            await anthropic_client.submit("s", "p", "m", "k")
        assert rec.calls == 1

    @pytest.mark.asyncio
    async def test_a_dropped_connection_is_retried(self, monkeypatch, no_sleep):
        rec = _Recorder(httpx.ConnectError("connection reset"), _json(200, _ANTHROPIC_OK))
        _patch_transport(monkeypatch, anthropic_client, rec)
        assert await anthropic_client.submit("s", "p", "m", "k") == "hello"
        assert rec.calls == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "module,ok",
        [(openai_client, _OPENAI_OK), (google_client, _GOOGLE_OK)],
    )
    async def test_every_provider_retries_not_just_one(self, monkeypatch, no_sleep, module, ok):
        rec = _Recorder(_json(429, {"error": "later"}), _json(200, ok))
        _patch_transport(monkeypatch, module, rec)
        assert await module.submit("s", "p", "m", "k") == "hello"
        assert rec.calls == 2
