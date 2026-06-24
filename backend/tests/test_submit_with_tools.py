"""Tests for native provider tool-calling: client.submit_with_tools (L4).

Each hosted client (anthropic / openai / google) translates the loop's provider-agnostic
messages + tools into its native tool-calling request, POSTs it, and parses the response back
into the normalized ToolUseResult (a final text answer OR a list of tool calls). The crux is
the response parsing, which differs per provider; these tests pin both paths for all three,
using respx to mock the HTTP so no real provider is contacted. Multi-turn tool-history
translation fidelity is a v1 best-effort (see LEARNINGS); these tests cover the single-turn
request and the parse.
"""

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
