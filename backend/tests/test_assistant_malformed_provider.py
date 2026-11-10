"""Malformed provider responses at the assistant message boundary, for every hosted provider.

Before slice A an Anthropic `{}` became an empty final answer, a `null` content part escaped as an
AttributeError (HTTP 500), and array tool arguments reached the tool executor. Each now ends the
turn as a controlled provider error: no answer is recorded, nothing raw reaches the response, and
an expected timeout maps to the existing 504.
"""

import httpx
import pytest
import respx

from app.services import llm_provider_config_service

pytestmark = pytest.mark.asyncio

API_KEY = "SYNTH-ASSISTANT-KEY-0123456789"
URLS = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "google": "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _conversation(client, session, user, token, provider):
    await llm_provider_config_service.upsert(
        session, org_id=user.organization_id, provider=provider, api_key=API_KEY, model="m", actor_user_id=user.id
    )
    await llm_provider_config_service.set_active(
        session, org_id=user.organization_id, provider=provider, actor_user_id=user.id
    )
    await session.commit()
    return (await client.post("/api/assistant/conversations", json={}, headers=_auth(token))).json()["id"]


async def _messages(client, token, conv_id):
    resp = await client.get(f"/api/assistant/conversations/{conv_id}/messages", headers=_auth(token))
    return [(m["role"], m["content"]) for m in resp.json()["messages"]]


CASES = [
    ("anthropic", httpx.Response(200, json={}), 502, "could not use"),
    ("anthropic", httpx.Response(200, json={"content": [None]}), 502, "could not use"),
    (
        "anthropic",
        httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "id": "t1", "name": "list_experiments", "input": [1]}],
                "stop_reason": "tool_use",
            },
        ),
        502,
        "could not use",
    ),
    (
        "anthropic",
        httpx.Response(200, json={"content": [{"type": "text", "text": "par"}], "stop_reason": "max_tokens"}),
        502,
        "stopped before finishing",
    ),
    ("anthropic", httpx.ReadTimeout("SYNTH-RAW"), 504, "did not respond in time"),
    ("google", httpx.Response(200, json=[]), 502, "could not use"),
    ("google", httpx.Response(200, json={"candidates": [{"content": {"parts": [None]}}]}), 502, "could not use"),
    (
        "google",
        httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"functionCall": {"name": "list_experiments", "args": [1]}}]}}]
            },
        ),
        502,
        "could not use",
    ),
    ("google", httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}), 502, "stopped before"),
    ("google", httpx.ReadTimeout("SYNTH-RAW"), 504, "did not respond in time"),
    ("google", httpx.Response(504, text="SYNTH-RAW"), 504, "did not respond in time"),
    (
        "google",
        httpx.Response(200, json={"candidates": [{"finishReason": [], "content": {"parts": [{"text": "a"}]}}]}),
        502,
        "could not use",
    ),
    ("google", httpx.Response(200, json={"promptFeedback": {"blockReason": ["SAFETY"]}}), 502, "could not use"),
    ("openai", httpx.Response(200, json=[]), 502, "could not use"),
    ("openai", httpx.Response(200, json={"status": [], "output": []}), 502, "could not use"),
    ("openai", httpx.Response(200, json={"status": "completed", "output": [None]}), 502, "could not use"),
]


@pytest.mark.parametrize(
    "provider,upstream,expected_status,fragment",
    CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)],
)
async def test_an_unusable_provider_turn_is_a_controlled_error(
    client, session, admin_user, admin_token, provider, upstream, expected_status, fragment
):
    conv_id = await _conversation(client, session, admin_user, admin_token, provider)
    with respx.mock:
        route = respx.post(url__startswith=URLS[provider])
        if isinstance(upstream, Exception):
            route.mock(side_effect=upstream)
        else:
            route.mock(return_value=upstream)
        response = await client.post(
            f"/api/assistant/conversations/{conv_id}/messages", json={"text": "hello"}, headers=_auth(admin_token)
        )
    assert response.status_code == expected_status, response.text
    assert fragment in response.json()["detail"]
    assert "SYNTH-RAW" not in response.text and API_KEY not in response.text
    # No answer, tool call, or plan was recorded for the failed turn.
    assert await _messages(client, admin_token, conv_id) == [("user", "hello")]
