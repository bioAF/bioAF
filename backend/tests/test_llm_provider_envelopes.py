"""Malformed provider responses stay inside the ProviderError contract.

Every hosted client (OpenAI, Anthropic, Google) is driven through its real HTTP
path with controlled responses. A successful HTTP status with an unusable body
must raise a typed ProviderError with a nonempty message, never return an
empty completion, never hand non-object tool arguments to the loop, and never
escape as an AttributeError/TypeError. Status and transport failures carry the
safe metadata (provider, upstream status, allowlisted code, timeout) that the
OpenAI Responses path already had.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import respx

from app.services.llm_models_fetch_service import list_models_with_fallback
from app.services.llm_provider_clients import ProviderError, anthropic_client, google_client, openai_client
from app.services.llm_provider_models import FALLBACK_MODELS

OPENAI = "https://api.openai.com/v1"
ANTHROPIC = "https://api.anthropic.com/v1"
GOOGLE = "https://generativelanguage.googleapis.com/v1beta"
KEY = "SYNTH-LLM-KEY-0123456789"

MODEL_LIST_URL = {
    "openai": f"{OPENAI}/models",
    "anthropic": f"{ANTHROPIC}/models",
    "google": f"{GOOGLE}/models",
}
CLIENTS = {"openai": openai_client, "anthropic": anthropic_client, "google": google_client}
SUBMIT_URL = {
    "openai": f"{OPENAI}/chat/completions",
    "anthropic": f"{ANTHROPIC}/messages",
    "google": f"{GOOGLE}/models/m:generateContent",
}
TOOL_URL = {
    "anthropic": f"{ANTHROPIC}/messages",
    "google": f"{GOOGLE}/models/m:generateContent",
    "openai": f"{OPENAI}/responses",
}
TOOLS = [{"name": "list_experiments", "description": "List", "args_schema": {"properties": {}, "required": []}}]
MESSAGES = [{"role": "user", "content": "hi"}]


def _respond(method: str, url: str, response):
    route = respx.route(method=method, url__startswith=url)
    if isinstance(response, BaseException):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    return route


async def _submit(provider: str) -> str:
    return await CLIENTS[provider].submit(prompt="p", payload="x", model="m", api_key=KEY)


async def _tools(provider: str):
    return await CLIENTS[provider].submit_with_tools(messages=MESSAGES, tools=TOOLS, model="m", api_key=KEY)


def _assert_provider_error(err: ProviderError, provider: str, error_class: str = "parse") -> None:
    assert err.error_class == error_class
    assert str(err).strip()
    assert err.provider == provider
    assert KEY not in str(err)


# --- Model listing ------------------------------------------------------------

MALFORMED_MODEL_LISTS = {
    "top-level array": httpx.Response(200, json=[]),
    "top-level null": httpx.Response(200, json=None),
    "top-level scalar": httpx.Response(200, json=7),
    "invalid json": httpx.Response(200, text="<html>maintenance</html>"),
    "items not a list": None,  # filled per provider
    "item not an object": None,
    "item id not a string": None,
}


def _malformed_model_list(provider: str, case: str) -> httpx.Response:
    fixed = MALFORMED_MODEL_LISTS[case]
    if fixed is not None:
        return fixed
    key, field = ("models", "name") if provider == "google" else ("data", "id")
    if case == "items not a list":
        return httpx.Response(200, json={key: "gpt"})
    if case == "item not an object":
        return httpx.Response(200, json={key: ["gpt"]})
    return httpx.Response(200, json={key: [{field: 12}]})


@pytest.mark.parametrize("case", sorted(MALFORMED_MODEL_LISTS))
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_a_malformed_model_list_is_a_parse_error(provider, case):
    with respx.mock:
        _respond("GET", MODEL_LIST_URL[provider], _malformed_model_list(provider, case))
        with pytest.raises(ProviderError) as info:
            await CLIENTS[provider].list_models(KEY)
    _assert_provider_error(info.value, provider)


@pytest.mark.parametrize("case", sorted(MALFORMED_MODEL_LISTS))
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_a_malformed_model_list_falls_back(provider, case):
    with respx.mock:
        _respond("GET", MODEL_LIST_URL[provider], _malformed_model_list(provider, case))
        models, used_fallback = await list_models_with_fallback(provider, KEY)
    assert models == FALLBACK_MODELS[provider]
    assert used_fallback is True


@pytest.mark.parametrize(
    "upstream",
    [
        httpx.Response(401, text="bad"),
        httpx.Response(429, text="slow down"),
        httpx.Response(503, text="down"),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
    ids=["auth", "rate_limit", "server", "transport", "timeout"],
)
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_expected_model_list_failures_fall_back(provider, upstream):
    with respx.mock:
        _respond("GET", MODEL_LIST_URL[provider], upstream)
        models, used_fallback = await list_models_with_fallback(provider, KEY)
    assert models == FALLBACK_MODELS[provider]
    assert used_fallback is True


@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_cancellation_is_not_a_model_list_fallback(provider):
    def cancel(request):
        raise asyncio.CancelledError()

    with respx.mock:
        respx.route(method="GET", url__startswith=MODEL_LIST_URL[provider]).mock(side_effect=cancel)
        with pytest.raises(asyncio.CancelledError):
            await list_models_with_fallback(provider, KEY)


@pytest.mark.asyncio
async def test_gemma_model_list_is_still_static():
    models, used_fallback = await list_models_with_fallback("gemma", None)
    assert models == FALLBACK_MODELS["gemma"]
    assert used_fallback is False


# --- Non-tool completions -----------------------------------------------------

MALFORMED_COMPLETIONS = {
    "openai": {
        "top-level array": [],
        "top-level null": None,
        "no choices": {"choices": []},
        "choice not an object": {"choices": ["x"]},
        "content null": {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
        "content empty": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
        "content not text": {"choices": [{"message": {"content": 12}, "finish_reason": "stop"}]},
    },
    "anthropic": {
        "top-level array": [],
        "top-level null": None,
        "empty object": {},
        "content not a list": {"content": "hi"},
        "null content part": {"content": [None]},
        "text not a string": {"content": [{"type": "text", "text": 5}]},
        "no text parts": {"content": [{"type": "thinking", "thinking": "..."}], "stop_reason": "end_turn"},
    },
    "google": {
        "top-level array": [],
        "top-level null": None,
        "no candidates": {"candidates": []},
        "candidate not an object": {"candidates": [None]},
        "null content part": {"candidates": [{"content": {"parts": [None]}, "finishReason": "STOP"}]},
        "parts not a list": {"candidates": [{"content": {"parts": {}}, "finishReason": "STOP"}]},
        "text not a string": {"candidates": [{"content": {"parts": [{"text": 1}]}, "finishReason": "STOP"}]},
        "empty text": {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}]},
    },
}


def _completion_cases():
    for provider, cases in MALFORMED_COMPLETIONS.items():
        for label, body in cases.items():
            yield pytest.param(provider, body, id=f"{provider}-{label}")


@pytest.mark.parametrize("provider,body", list(_completion_cases()))
@pytest.mark.asyncio
async def test_an_unusable_completion_is_a_parse_error(provider, body):
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    _assert_provider_error(info.value, provider)


@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_invalid_json_completion_is_a_parse_error(provider):
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(200, text="not json"))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    _assert_provider_error(info.value, provider)


TRUNCATED = {
    "openai": ({"choices": [{"message": {"content": "half"}, "finish_reason": "length"}]}, "length"),
    "anthropic": ({"content": [{"type": "text", "text": "half"}], "stop_reason": "max_tokens"}, "max_tokens"),
    "google": (
        {"candidates": [{"content": {"parts": [{"text": "half"}]}, "finishReason": "MAX_TOKENS"}]},
        "MAX_TOKENS",
    ),
}


@pytest.mark.parametrize("provider", sorted(TRUNCATED))
@pytest.mark.asyncio
async def test_a_truncated_completion_is_incomplete_not_an_answer(provider):
    body, code = TRUNCATED[provider]
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    _assert_provider_error(info.value, provider, "incomplete")
    assert info.value.upstream_code == code


FILTERED = {
    "openai": ({"choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]}, "content_filter"),
    "google": ({"candidates": [{"finishReason": "SAFETY"}]}, "SAFETY"),
}


@pytest.mark.parametrize("provider", sorted(FILTERED))
@pytest.mark.asyncio
async def test_a_filtered_completion_is_incomplete_with_its_reason(provider):
    body, code = FILTERED[provider]
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    _assert_provider_error(info.value, provider, "incomplete")
    assert info.value.upstream_code == code


@pytest.mark.asyncio
async def test_a_google_prompt_block_is_incomplete_with_its_reason():
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    with respx.mock:
        _respond("POST", SUBMIT_URL["google"], httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as info:
            await _submit("google")
    _assert_provider_error(info.value, "google", "incomplete")
    assert info.value.upstream_code == "SAFETY"


@pytest.mark.asyncio
async def test_an_openai_refusal_is_returned_as_the_text():
    body = {"choices": [{"message": {"content": None, "refusal": "I can't help with that."}, "finish_reason": "stop"}]}
    with respx.mock:
        _respond("POST", SUBMIT_URL["openai"], httpx.Response(200, json=body))
        assert await _submit("openai") == "I can't help with that."


VALID_COMPLETIONS = {
    "openai": {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]},
    "anthropic": {
        "content": [{"type": "text", "text": "ans"}, {"type": "text", "text": "wer"}],
        "stop_reason": "end_turn",
    },
    "google": {"candidates": [{"content": {"parts": [{"text": "ans"}, {"text": "wer"}]}, "finishReason": "STOP"}]},
}


@pytest.mark.parametrize("provider", sorted(VALID_COMPLETIONS))
@pytest.mark.asyncio
async def test_a_valid_completion_still_returns_its_text(provider):
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(200, json=VALID_COMPLETIONS[provider]))
        assert await _submit(provider) == "answer"


# --- Tool turns ---------------------------------------------------------------

MALFORMED_TOOL_TURNS = {
    "anthropic": {
        "empty object": {},
        "null content part": {"content": [None], "stop_reason": "end_turn"},
        "array arguments": {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "list_experiments", "input": [1, 2]}],
            "stop_reason": "tool_use",
        },
        "string arguments": {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "list_experiments", "input": "x"}],
            "stop_reason": "tool_use",
        },
        "missing call id": {
            "content": [{"type": "tool_use", "name": "list_experiments", "input": {}}],
            "stop_reason": "tool_use",
        },
        "missing name": {"content": [{"type": "tool_use", "id": "toolu_1", "input": {}}], "stop_reason": "tool_use"},
        "text not a string": {"content": [{"type": "text", "text": ["a"]}], "stop_reason": "end_turn"},
    },
    "google": {
        "top-level array": [],
        "no candidates": {"candidates": []},
        "null content part": {"candidates": [{"content": {"parts": [None]}, "finishReason": "STOP"}]},
        "array arguments": {
            "candidates": [
                {
                    "content": {"parts": [{"functionCall": {"name": "list_experiments", "args": [1]}}]},
                    "finishReason": "STOP",
                }
            ]
        },
        "missing name": {
            "candidates": [{"content": {"parts": [{"functionCall": {"args": {}}}]}, "finishReason": "STOP"}]
        },
        "function call not an object": {
            "candidates": [{"content": {"parts": [{"functionCall": "x"}]}, "finishReason": "STOP"}]
        },
        "no text or calls": {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]},
    },
    "openai": {
        "top-level array": [],
        "output item not an object": {"status": "completed", "output": [None]},
        "message content not a list": {
            "status": "completed",
            "output": [{"type": "message", "content": "text"}],
        },
    },
}


def _tool_cases():
    for provider, cases in MALFORMED_TOOL_TURNS.items():
        for label, body in cases.items():
            yield pytest.param(provider, body, id=f"{provider}-{label}")


@pytest.mark.parametrize("provider,body", list(_tool_cases()))
@pytest.mark.asyncio
async def test_an_unusable_tool_turn_is_a_parse_error(provider, body):
    with respx.mock:
        _respond("POST", TOOL_URL[provider], httpx.Response(200, json=body))
        with pytest.raises(ProviderError) as info:
            await _tools(provider)
    _assert_provider_error(info.value, provider)


TRUNCATED_TOOL_TURNS = {
    "anthropic": {
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "list_experiments", "input": {}}],
        "stop_reason": "max_tokens",
    },
    "google": {"candidates": [{"content": {"parts": [{"text": "half"}]}, "finishReason": "MAX_TOKENS"}]},
}


@pytest.mark.parametrize("provider", sorted(TRUNCATED_TOOL_TURNS))
@pytest.mark.asyncio
async def test_a_truncated_tool_turn_is_incomplete(provider):
    with respx.mock:
        _respond("POST", TOOL_URL[provider], httpx.Response(200, json=TRUNCATED_TOOL_TURNS[provider]))
        with pytest.raises(ProviderError) as info:
            await _tools(provider)
    _assert_provider_error(info.value, provider, "incomplete")


@pytest.mark.asyncio
async def test_a_valid_anthropic_tool_turn_keeps_its_identity():
    body = {
        "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "toolu_1", "name": "list_experiments", "input": {"limit": 2}},
        ],
        "stop_reason": "tool_use",
    }
    with respx.mock:
        _respond("POST", TOOL_URL["anthropic"], httpx.Response(200, json=body))
        turn = await _tools("anthropic")
    assert turn.text == "Looking."
    assert [(c.tool, c.args, c.id) for c in turn.tool_calls] == [("list_experiments", {"limit": 2}, "toolu_1")]


@pytest.mark.asyncio
async def test_a_google_call_without_args_has_empty_object_args():
    body = {
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "list_experiments"}}]}, "finishReason": "STOP"}]
    }
    with respx.mock:
        _respond("POST", TOOL_URL["google"], httpx.Response(200, json=body))
        turn = await _tools("google")
    assert [(c.tool, c.args) for c in turn.tool_calls] == [("list_experiments", {})]


# --- Status and transport metadata --------------------------------------------

ERROR_BODIES = {
    "openai": {"error": {"message": "SYNTH-PROVIDER-BODY", "type": "invalid_request_error", "code": "model_not_found"}},
    "anthropic": {"type": "error", "error": {"type": "rate_limit_error", "message": "SYNTH-PROVIDER-BODY"}},
    "google": {"error": {"code": 429, "message": "SYNTH-PROVIDER-BODY", "status": "RESOURCE_EXHAUSTED"}},
}
EXPECTED_CODES = {"openai": "model_not_found", "anthropic": "rate_limit_error", "google": "RESOURCE_EXHAUSTED"}


@pytest.mark.parametrize("operation", ["list_models", "submit", "tools"])
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_status_failures_carry_safe_metadata(provider, operation, caplog):
    if operation == "list_models":
        method, url, call = "GET", MODEL_LIST_URL[provider], lambda: CLIENTS[provider].list_models(KEY)
    elif operation == "submit":
        method, url, call = "POST", SUBMIT_URL[provider], lambda: _submit(provider)
    else:
        method, url, call = "POST", TOOL_URL[provider], lambda: _tools(provider)
    caplog.set_level(logging.DEBUG, logger="bioaf")
    with respx.mock:
        _respond(method, url, httpx.Response(429, json=ERROR_BODIES[provider]))
        with pytest.raises(ProviderError) as info:
            await call()
    err = info.value
    assert err.error_class == "rate_limit"
    assert err.upstream_status == 429
    assert err.upstream_code == EXPECTED_CODES[provider]
    assert err.provider == provider
    assert str(err).strip()
    assert "SYNTH-PROVIDER-BODY" not in caplog.text
    assert KEY not in caplog.text


@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_an_unrecognized_upstream_code_is_dropped(provider):
    body = {"error": {"code": "acct_SYNTH12345", "type": "acct_SYNTH12345", "status": "acct_SYNTH12345"}}
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(400, json=body))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    assert info.value.upstream_code is None
    assert info.value.upstream_status == 400


@pytest.mark.parametrize("status", [408, 504])
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_upstream_timeout_statuses_are_timeouts(provider, status):
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(status, text="slow"))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    assert info.value.timeout is True


@pytest.mark.parametrize("operation", ["list_models", "submit", "tools"])
@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_transport_failures_carry_timeout_metadata_and_no_raw_text(provider, operation, caplog):
    if operation == "list_models":
        method, url, call = "GET", MODEL_LIST_URL[provider], lambda: CLIENTS[provider].list_models(KEY)
    elif operation == "submit":
        method, url, call = "POST", SUBMIT_URL[provider], lambda: _submit(provider)
    else:
        method, url, call = "POST", TOOL_URL[provider], lambda: _tools(provider)
    caplog.set_level(logging.DEBUG, logger="bioaf")
    for exc, is_timeout in (
        (httpx.ReadTimeout(f"SYNTH-RAW {KEY}"), True),
        (httpx.ConnectError(f"SYNTH-RAW {KEY}"), False),
    ):
        with respx.mock:
            _respond(method, url, exc)
            with pytest.raises(ProviderError) as info:
                await call()
        err = info.value
        assert err.error_class == "transport"
        assert err.timeout is is_timeout
        assert err.provider == provider
        assert type(exc).__name__ in str(err)
        assert "SYNTH-RAW" not in str(err)
    assert "SYNTH-RAW" not in caplog.text
    assert KEY not in caplog.text


@pytest.mark.parametrize("provider", sorted(CLIENTS))
@pytest.mark.asyncio
async def test_a_status_failure_with_an_empty_body_still_has_a_message(provider):
    with respx.mock:
        _respond("POST", SUBMIT_URL[provider], httpx.Response(500, text=""))
        with pytest.raises(ProviderError) as info:
            await _submit(provider)
    assert "500" in str(info.value)
