"""OpenAI Responses API translation for the assistant tool path (plan 12.5, finding L2).

The configured model (gpt-6.1-sol) only supports tool calling through POST /v1/responses. These
tests pin the request the adapter sends and how each response shape is translated back into the
normalized ToolUseResult, using recorded-shape fixtures and no real provider.
"""

import json

import httpx
import pytest
import respx

from app.services.llm_provider_clients import ProviderError, openai_client

pytestmark = pytest.mark.asyncio

MODEL = "gpt-6.1-sol"
TOOLS = [
    {
        "name": "launch_run",
        "description": "Propose a pipeline run.",
        "args_schema": {
            "required": ["experiment_id", "pipeline_key"],
            "properties": {
                "experiment_id": {"type": "integer"},
                "pipeline_key": {"type": "string"},
                "sample_ids": {"type": "array"},
            },
        },
    }
]
USER = [{"role": "user", "content": "run rnaseq on 7", "tool_calls": None, "tool_invocation_id": None}]

REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC-OPAQUE-1"}
COMMENTARY = {
    "type": "message",
    "id": "msg_c",
    "role": "assistant",
    "status": "completed",
    "phase": "commentary",
    "content": [{"type": "output_text", "text": "Checking first.", "annotations": []}],
}


def _fc(call_id, name, args, item_id=None):
    return {
        "type": "function_call",
        "id": item_id or f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": args if isinstance(args, str) else json.dumps(args),
        "status": "completed",
    }


def _msg(text, phase="final_answer"):
    return {
        "type": "message",
        "id": "msg_f",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response(*output, status="completed", **extra):
    return httpx.Response(
        200, json={"id": "resp_1", "object": "response", "status": status, "output": list(output), **extra}
    )


async def _submit(response, messages=USER, system="SYSTEM"):
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/responses").mock(return_value=response)
        try:
            result = await openai_client.submit_with_tools(
                messages=messages, tools=TOOLS, model=MODEL, api_key="sk-SYNTH-KEY", system=system
            )
        finally:
            body = json.loads(route.calls.last.request.content) if route.calls else None
    return result, body


async def test_request_uses_responses_with_the_configured_model_and_no_forced_reasoning():
    _, body = await _submit(_response(_msg("hi")))
    assert body["model"] == MODEL
    assert body["store"] is False
    assert body["instructions"] == "SYSTEM"
    assert body["input"] == [{"role": "user", "content": "run rnaseq on 7"}]
    assert "reasoning" not in body
    assert "messages" not in body
    assert body["tools"] == [
        {
            "type": "function",
            "name": "launch_run",
            "description": "Propose a pipeline run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "integer"},
                    "pipeline_key": {"type": "string"},
                    "sample_ids": {"type": "array"},
                },
                "required": ["experiment_id", "pipeline_key"],
            },
            # Optional arguments (sample_ids) stay optional.
            "strict": False,
        }
    ]


async def test_several_function_calls_keep_call_ids_distinct_from_item_ids():
    result, _ = await _submit(
        _response(
            REASONING,
            _fc("call_a", "launch_run", {"experiment_id": 7, "pipeline_key": "nf-core/rnaseq"}, item_id="fc_x"),
            _fc("call_b", "launch_run", {"experiment_id": 8, "pipeline_key": "nf-core/rnaseq"}, item_id="fc_y"),
        )
    )
    assert [c.id for c in result.tool_calls] == ["call_a", "call_b"]
    assert [c.args["experiment_id"] for c in result.tool_calls] == [7, 8]
    assert result.is_final is False


async def test_mixed_output_items_are_kept_in_order_as_provider_state():
    output = [REASONING, COMMENTARY, _fc("call_a", "launch_run", {"experiment_id": 7, "pipeline_key": "k"})]
    result, _ = await _submit(_response(*output))
    assert result.text == "Checking first."
    assert [c.id for c in result.tool_calls] == ["call_a"]
    assert result.provider_state == {"provider": "openai", "model": MODEL, "items": output}


async def test_final_answer_turn_also_returns_its_items():
    output = [REASONING, _msg("Use nf-core/rnaseq.")]
    result, _ = await _submit(_response(*output))
    assert result.is_final and result.text == "Use nf-core/rnaseq."
    assert result.provider_state["items"] == output


async def test_refusal_is_returned_as_the_answer_text():
    refusal = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "refusal", "refusal": "I can't help with that."}],
    }
    result, _ = await _submit(_response(refusal))
    assert result.is_final and result.text == "I can't help with that."


@pytest.mark.parametrize("arguments", ['{"experiment_id": 7', "[1, 2]", "null"])
async def test_malformed_arguments_are_an_unusable_response(arguments):
    with pytest.raises(ProviderError) as info:
        await _submit(_response(_fc("call_a", "launch_run", arguments)))
    assert info.value.error_class == "parse"


async def test_completed_response_without_text_or_calls_is_unusable():
    with pytest.raises(ProviderError) as info:
        await _submit(_response(REASONING))
    assert info.value.error_class == "parse"


async def test_incomplete_response_is_not_an_answer():
    with pytest.raises(ProviderError) as info:
        await _submit(
            _response(_msg("partial"), status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        )
    assert info.value.error_class == "incomplete"


async def test_failed_response_is_not_an_answer():
    with pytest.raises(ProviderError) as info:
        await _submit(_response(status="failed", error={"code": "server_error", "message": "boom"}))
    assert info.value.error_class == "server"


@pytest.mark.parametrize(
    "status,error_class",
    [(400, "other"), (401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "server"), (503, "server")],
)
async def test_http_errors_carry_status_and_structured_code(status, error_class):
    body = {
        "error": {
            "message": "SYNTH-PROVIDER-BODY Function tools are only supported in v1/responses",
            "type": "invalid_request_error",
            "param": "tools",
            "code": "unsupported_parameter",
        }
    }
    with pytest.raises(ProviderError) as info:
        await _submit(httpx.Response(status, json=body))
    assert info.value.error_class == error_class
    assert info.value.upstream_status == status
    assert info.value.upstream_code == "unsupported_parameter"
    assert info.value.timeout is False


async def test_unsafe_upstream_codes_are_not_carried():
    body = {"error": {"code": "has spaces and SYNTH-PROVIDER-BODY", "type": "x" * 200}}
    with pytest.raises(ProviderError) as info:
        await _submit(httpx.Response(400, json=body))
    assert info.value.upstream_code is None


async def test_timeout_is_distinguished_from_other_transport_failures():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(side_effect=httpx.ReadTimeout("SYNTH-URL timed out"))
        with pytest.raises(ProviderError) as info:
            await openai_client.submit_with_tools(messages=USER, tools=TOOLS, model=MODEL, api_key="sk")
    assert info.value.error_class == "transport" and info.value.timeout is True

    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/responses").mock(side_effect=httpx.ConnectError("SYNTH-URL refused"))
        with pytest.raises(ProviderError) as info:
            await openai_client.submit_with_tools(messages=USER, tools=TOOLS, model=MODEL, api_key="sk")
    assert info.value.error_class == "transport" and info.value.timeout is False


async def test_replay_sends_stored_items_verbatim_then_outputs_by_call_id():
    state_items = [REASONING, COMMENTARY, _fc("call_a", "launch_run", {"experiment_id": 7, "pipeline_key": "k"})]
    final_items = [{**REASONING, "id": "rs_2", "encrypted_content": "ENC-OPAQUE-2"}, _msg("Proposed.")]
    history = [
        *USER,
        {
            "role": "assistant",
            "content": "Checking first.",
            "tool_calls": [
                {"tool": "launch_run", "args": {"experiment_id": 7, "pipeline_key": "k"}, "call_id": "call_a"}
            ],
            "tool_invocation_id": None,
            "provider_state": {"v": 1, "provider": "openai", "model": MODEL, "items": state_items},
        },
        {"role": "tool", "content": "PENDING", "tool_calls": None, "tool_invocation_id": 5, "tool_call_id": "call_a"},
        {
            "role": "assistant",
            "content": "Proposed.",
            "tool_calls": None,
            "tool_invocation_id": None,
            "provider_state": {"v": 1, "provider": "openai", "model": MODEL, "items": final_items},
        },
        {"role": "user", "content": "go on", "tool_calls": None, "tool_invocation_id": None},
    ]
    _, body = await _submit(_response(_msg("ok")), messages=history)
    assert body["input"] == [
        {"role": "user", "content": "run rnaseq on 7"},
        *state_items,
        {"type": "function_call_output", "call_id": "call_a", "output": "PENDING"},
        *final_items,
        {"role": "user", "content": "go on"},
    ]


async def test_history_without_state_is_rebuilt_from_normalized_calls():
    history = [
        *USER,
        {
            "role": "assistant",
            "content": "Let me look.",
            "tool_calls": [{"tool": "launch_run", "args": {"experiment_id": 7}, "call_id": "call_z"}],
            "tool_invocation_id": None,
        },
        {"role": "tool", "content": "RESULT", "tool_calls": None, "tool_invocation_id": 5, "tool_call_id": "call_z"},
        {"role": "assistant", "content": "Done.", "tool_calls": None, "tool_invocation_id": None},
    ]
    _, body = await _submit(_response(_msg("ok")), messages=history)
    assert body["input"] == [
        {"role": "user", "content": "run rnaseq on 7"},
        {"role": "assistant", "content": "Let me look."},
        {"type": "function_call", "call_id": "call_z", "name": "launch_run", "arguments": '{"experiment_id": 7}'},
        {"type": "function_call_output", "call_id": "call_z", "output": "RESULT"},
        {"role": "assistant", "content": "Done."},
    ]
