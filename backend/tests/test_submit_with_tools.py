"""Tests for native provider tool-calling: client.submit_with_tools (L4).

Each hosted client (anthropic / openai / google) translates the loop's provider-agnostic
messages + tools into its native tool-calling request, POSTs it, and parses the response back
into the normalized ToolUseResult (a final text answer OR a list of tool calls). The crux is
the response parsing, which differs per provider; these tests pin both paths for all three,
using respx to mock the HTTP so no real provider is contacted. All three providers now thread
prior tool exchanges as native blocks (Anthropic tool_use/tool_result; OpenAI tool_calls +
tool-role messages with tool_call_id; Google functionCall/functionResponse). These tests cover
the response parse for all three plus, per provider, the native multi-turn request and the
synthesized result for a tool call left unanswered by a spend stop.
"""

import json

import httpx
import pytest
import respx

from app.services.llm_provider_clients import anthropic_client, google_client, openai_client
from app.services.llm_provider_clients.tool_use import ToolUseResult

pytestmark = pytest.mark.asyncio

_TOOLS = [
    {
        "name": "recommend_pipeline",
        "description": "Recommend a pipeline for an experiment.",
        "args_schema": {"required": ["experiment_id"], "properties": {"experiment_id": {"type": "integer"}}},
    }
]
_MESSAGES = [
    {"role": "user", "content": "recommend a pipeline for experiment 7", "tool_calls": None, "tool_invocation_id": None}
]


def _resp(json_body):
    return httpx.Response(200, json=json_body)


# ---- Anthropic ----


async def test_anthropic_parses_tool_call():
    raw = {
        "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "tu_1", "name": "recommend_pipeline", "input": {"experiment_id": 7}},
        ],
        "stop_reason": "tool_use",
    }
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        route = r.post("/messages").mock(return_value=_resp(raw))
        result = await anthropic_client.submit_with_tools(
            messages=_MESSAGES, tools=_TOOLS, model="claude-x", api_key="sk-ant"
        )
    assert route.called
    assert isinstance(result, ToolUseResult)
    assert result.is_final is False
    assert result.tool_calls[0].tool == "recommend_pipeline"
    assert result.tool_calls[0].args == {"experiment_id": 7}


async def test_anthropic_parses_final_text():
    raw = {"content": [{"type": "text", "text": "I recommend nf-core/rnaseq."}], "stop_reason": "end_turn"}
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.post("/messages").mock(return_value=_resp(raw))
        result = await anthropic_client.submit_with_tools(
            messages=_MESSAGES, tools=_TOOLS, model="claude-x", api_key="sk-ant"
        )
    assert result.is_final is True
    assert "rnaseq" in result.text


# ---- Anthropic native multi-turn tool history (the live-test fix) ----


async def test_anthropic_threads_prior_tools_as_native_blocks():
    """Prior tool exchanges must be sent as native tool_use / tool_result blocks, not plain text.
    The plain-text encoding made the model parrot '[called tools: ...]' as text instead of issuing
    a real tool call, so a multi-step conversation stalled. The request must carry native blocks
    with matching ids and no plain-text leakage."""
    history = [
        {"role": "user", "content": "list samples for experiment 3", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": "Let me check the samples.",
            "tool_calls": [{"tool": "list_samples", "args": {"experiment_id": 3}}],
            "tool_invocation_id": None,
        },
        {
            "role": "tool",
            "content": '{"status": "succeeded", "result": {"samples": [{"id": 1}]}}',
            "tool_calls": None,
            "tool_invocation_id": 11,
        },
    ]
    raw = {"content": [{"type": "text", "text": "Here is the recommendation."}], "stop_reason": "end_turn"}
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        route = r.post("/messages").mock(return_value=_resp(raw))
        await anthropic_client.submit_with_tools(messages=history, tools=_TOOLS, model="claude-x", api_key="sk-ant")

    body = json.loads(route.calls.last.request.content)
    msgs = body["messages"]

    assistant_msg = next(m for m in msgs if m["role"] == "assistant")
    blocks = assistant_msg["content"]
    assert isinstance(blocks, list)
    tool_use = next(b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
    assert tool_use["name"] == "list_samples"
    assert tool_use["input"] == {"experiment_id": 3}

    # The result is a native tool_result in a user message, referencing the same id.
    tool_result = None
    for m in msgs:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tool_result = b
    assert tool_result is not None
    assert tool_result["tool_use_id"] == tool_use["id"]

    raw_text = route.calls.last.request.content.decode()
    assert "[called tools:" not in raw_text
    assert "[tool result]" not in raw_text


async def test_anthropic_synthesizes_result_for_unanswered_tool_use():
    """A spend stop leaves an assistant tool_use with no persisted result (the loop halts for
    confirmation). On a later turn that dangling tool_use must still get a tool_result, or Anthropic
    rejects the request. The translator synthesizes a placeholder result."""
    history = [
        {"role": "user", "content": "run it", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"tool": "launch_run", "args": {"experiment_id": 3, "pipeline_key": "nf-core/rnaseq"}}],
            "tool_invocation_id": None,
        },
        {"role": "user", "content": "actually wait", "tool_calls": None, "tool_invocation_id": None},
    ]
    raw = {"content": [{"type": "text", "text": "Okay."}], "stop_reason": "end_turn"}
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        route = r.post("/messages").mock(return_value=_resp(raw))
        await anthropic_client.submit_with_tools(messages=history, tools=_TOOLS, model="claude-x", api_key="sk-ant")

    body = json.loads(route.calls.last.request.content)
    msgs = body["messages"]
    tool_use = next(
        b for m in msgs if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_use"
    )
    results = [
        b for m in msgs if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"
    ]
    assert any(b["tool_use_id"] == tool_use["id"] for b in results)


# ---- OpenAI ----


async def test_openai_parses_tool_call():
    raw = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "recommend_pipeline", "arguments": '{"experiment_id": 7}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/chat/completions").mock(return_value=_resp(raw))
        result = await openai_client.submit_with_tools(messages=_MESSAGES, tools=_TOOLS, model="gpt-x", api_key="sk")
    assert route.called
    assert result.is_final is False
    assert result.tool_calls[0].tool == "recommend_pipeline"
    assert result.tool_calls[0].args == {"experiment_id": 7}


async def test_openai_parses_final_text():
    raw = {
        "choices": [
            {"message": {"content": "I recommend nf-core/rnaseq.", "tool_calls": None}, "finish_reason": "stop"}
        ]
    }
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/chat/completions").mock(return_value=_resp(raw))
        result = await openai_client.submit_with_tools(messages=_MESSAGES, tools=_TOOLS, model="gpt-x", api_key="sk")
    assert result.is_final is True
    assert "rnaseq" in result.text


# ---- OpenAI native multi-turn tool history (parity) ----


async def test_openai_threads_prior_tools_as_native_messages():
    history = [
        {"role": "user", "content": "list samples for experiment 3", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [{"tool": "list_samples", "args": {"experiment_id": 3}}],
            "tool_invocation_id": None,
        },
        {
            "role": "tool",
            "content": '{"status": "succeeded", "result": {"samples": [{"id": 1}]}}',
            "tool_calls": None,
            "tool_invocation_id": 11,
        },
    ]
    raw = {"choices": [{"message": {"content": "ok", "tool_calls": None}, "finish_reason": "stop"}]}
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/chat/completions").mock(return_value=_resp(raw))
        await openai_client.submit_with_tools(messages=history, tools=_TOOLS, model="gpt-x", api_key="sk")

    body = json.loads(route.calls.last.request.content)
    msgs = body["messages"]
    assistant_msg = next(m for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
    call = assistant_msg["tool_calls"][0]
    assert call["function"]["name"] == "list_samples"
    assert json.loads(call["function"]["arguments"]) == {"experiment_id": 3}
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == call["id"]
    raw_text = route.calls.last.request.content.decode()
    assert "[called tools:" not in raw_text and "[tool result]" not in raw_text


async def test_openai_synthesizes_tool_message_for_unanswered_call():
    history = [
        {"role": "user", "content": "run it", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"tool": "launch_run", "args": {"experiment_id": 3, "pipeline_key": "nf-core/rnaseq"}}],
            "tool_invocation_id": None,
        },
        {"role": "user", "content": "actually wait", "tool_calls": None, "tool_invocation_id": None},
    ]
    raw = {"choices": [{"message": {"content": "ok", "tool_calls": None}, "finish_reason": "stop"}]}
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/chat/completions").mock(return_value=_resp(raw))
        await openai_client.submit_with_tools(messages=history, tools=_TOOLS, model="gpt-x", api_key="sk")

    body = json.loads(route.calls.last.request.content)
    msgs = body["messages"]
    call = next(m for m in msgs if m.get("tool_calls"))["tool_calls"][0]
    assert any(m["role"] == "tool" and m["tool_call_id"] == call["id"] for m in msgs)


# ---- Google ----


async def test_google_parses_tool_call():
    raw = {
        "candidates": [
            {"content": {"parts": [{"functionCall": {"name": "recommend_pipeline", "args": {"experiment_id": 7}}}]}}
        ]
    }
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        route = r.post("/models/gemini-x:generateContent").mock(return_value=_resp(raw))
        result = await google_client.submit_with_tools(messages=_MESSAGES, tools=_TOOLS, model="gemini-x", api_key="g")
    assert route.called
    assert result.is_final is False
    assert result.tool_calls[0].tool == "recommend_pipeline"
    assert result.tool_calls[0].args == {"experiment_id": 7}


async def test_google_parses_final_text():
    raw = {"candidates": [{"content": {"parts": [{"text": "I recommend nf-core/rnaseq."}]}}]}
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        r.post("/models/gemini-x:generateContent").mock(return_value=_resp(raw))
        result = await google_client.submit_with_tools(messages=_MESSAGES, tools=_TOOLS, model="gemini-x", api_key="g")
    assert result.is_final is True
    assert "rnaseq" in result.text


# ---- Google native multi-turn tool history (parity) ----


async def test_google_threads_prior_tools_as_native_parts():
    history = [
        {"role": "user", "content": "list samples for experiment 3", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [{"tool": "list_samples", "args": {"experiment_id": 3}}],
            "tool_invocation_id": None,
        },
        {
            "role": "tool",
            "content": '{"status": "succeeded", "result": {"samples": [{"id": 1}]}}',
            "tool_calls": None,
            "tool_invocation_id": 11,
        },
    ]
    raw = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        route = r.post("/models/gemini-x:generateContent").mock(return_value=_resp(raw))
        await google_client.submit_with_tools(messages=history, tools=_TOOLS, model="gemini-x", api_key="g")

    body = json.loads(route.calls.last.request.content)
    contents = body["contents"]
    model_turn = next(c for c in contents if c["role"] == "model")
    fc = next(p["functionCall"] for p in model_turn["parts"] if "functionCall" in p)
    assert fc["name"] == "list_samples"
    assert fc["args"] == {"experiment_id": 3}
    # The result is a native functionResponse part (an object response), keyed by the function name.
    fr = next(p["functionResponse"] for c in contents for p in c["parts"] if "functionResponse" in p)
    assert fr["name"] == "list_samples"
    assert isinstance(fr["response"], dict)
    raw_text = route.calls.last.request.content.decode()
    assert "[called tools:" not in raw_text and "[tool result]" not in raw_text


async def test_google_synthesizes_response_for_unanswered_call():
    history = [
        {"role": "user", "content": "run it", "tool_calls": None, "tool_invocation_id": None},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"tool": "launch_run", "args": {"experiment_id": 3, "pipeline_key": "nf-core/rnaseq"}}],
            "tool_invocation_id": None,
        },
        {"role": "user", "content": "actually wait", "tool_calls": None, "tool_invocation_id": None},
    ]
    raw = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        route = r.post("/models/gemini-x:generateContent").mock(return_value=_resp(raw))
        await google_client.submit_with_tools(messages=history, tools=_TOOLS, model="gemini-x", api_key="g")

    body = json.loads(route.calls.last.request.content)
    contents = body["contents"]
    names = [p["functionResponse"]["name"] for c in contents for p in c["parts"] if "functionResponse" in p]
    assert "launch_run" in names


# ---- System prompt threading (each provider puts it in its native place) ----


async def test_anthropic_includes_system_prompt():
    raw = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        route = r.post("/messages").mock(return_value=_resp(raw))
        await anthropic_client.submit_with_tools(
            messages=_MESSAGES, tools=_TOOLS, model="claude-x", api_key="sk-ant", system="GUIDE ME"
        )
    body = json.loads(route.calls.last.request.content)
    assert body["system"] == "GUIDE ME"


async def test_openai_prepends_system_message():
    raw = {"choices": [{"message": {"content": "ok"}}]}
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        route = r.post("/chat/completions").mock(return_value=_resp(raw))
        await openai_client.submit_with_tools(
            messages=_MESSAGES, tools=_TOOLS, model="gpt-x", api_key="sk", system="GUIDE ME"
        )
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][0] == {"role": "system", "content": "GUIDE ME"}


async def test_google_sets_system_instruction():
    raw = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        route = r.post("/models/gemini-x:generateContent").mock(return_value=_resp(raw))
        await google_client.submit_with_tools(
            messages=_MESSAGES, tools=_TOOLS, model="gemini-x", api_key="g", system="GUIDE ME"
        )
    body = json.loads(route.calls.last.request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "GUIDE ME"
