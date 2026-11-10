"""Hosted provider requests follow explicit request policies.

Model listing is a read: transient failures retry under MODEL_LIST_READ before
the built-in fallback applies. Completions and tool turns are paid POSTs: one
attempt, whatever fails, so no hidden replay multiplies inference cost.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.llm_models_fetch_service import list_models_with_fallback
from app.services.llm_provider_clients import ProviderError, anthropic_client, google_client, openai_client

pytestmark = pytest.mark.asyncio

KEY = "SYNTH-KEY-123456"
CLIENTS = {"openai": openai_client, "anthropic": anthropic_client, "google": google_client}
LIST_URL = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "google": "https://generativelanguage.googleapis.com/v1beta/models",
}
LIVE = {
    "openai": {"data": [{"id": "live-model"}]},
    "anthropic": {"data": [{"id": "live-model"}]},
    "google": {"models": [{"name": "models/live-model"}]},
}
POSTS = {
    ("openai", "submit"): "https://api.openai.com/v1/chat/completions",
    ("openai", "tools"): "https://api.openai.com/v1/responses",
    ("anthropic", "submit"): "https://api.anthropic.com/v1/messages",
    ("anthropic", "tools"): "https://api.anthropic.com/v1/messages",
    ("google", "submit"): "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
    ("google", "tools"): "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
}


def _route(method, url, *responses):
    script = list(responses)
    route = respx.route(method=method, url__startswith=url)

    def handler(request):
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return step

    route.mock(side_effect=handler)
    return route


@pytest.mark.parametrize("provider", sorted(CLIENTS))
async def test_a_transient_model_list_failure_is_retried_before_falling_back(provider):
    with respx.mock:
        route = _route("GET", LIST_URL[provider], httpx.Response(503), httpx.Response(200, json=LIVE[provider]))
        models, used_fallback = await list_models_with_fallback(provider, KEY)
    assert (models, used_fallback) == (["live-model"], False)
    assert route.call_count == 2


@pytest.mark.parametrize("provider", sorted(CLIENTS))
async def test_a_model_list_retry_honors_the_providers_hint(provider, request_clock):
    with respx.mock:
        _route(
            "GET",
            LIST_URL[provider],
            httpx.Response(429, headers={"Retry-After": "4"}),
            httpx.Response(200, json=LIVE[provider]),
        )
        assert await CLIENTS[provider].list_models(KEY) == ["live-model"]
    assert 4.0 in request_clock.slept


@pytest.mark.parametrize("provider", sorted(CLIENTS))
async def test_persistent_model_list_failure_is_bounded_then_falls_back(provider):
    with respx.mock:
        route = _route("GET", LIST_URL[provider], httpx.Response(503))
        _, used_fallback = await list_models_with_fallback(provider, KEY)
    assert used_fallback is True
    assert route.call_count == 3


@pytest.mark.parametrize("provider", sorted(CLIENTS))
async def test_an_auth_failure_is_not_retried(provider):
    with respx.mock:
        route = _route("GET", LIST_URL[provider], httpx.Response(401))
        with pytest.raises(ProviderError):
            await CLIENTS[provider].list_models(KEY)
    assert route.call_count == 1


TOOLS = [{"name": "t", "description": "d", "args_schema": {}}]


async def _post(provider, kind):
    client = CLIENTS[provider]
    if kind == "submit":
        return await client.submit(prompt="p", payload="x", model="m", api_key=KEY)
    return await client.submit_with_tools(
        messages=[{"role": "user", "content": "x"}], tools=TOOLS, model="m", api_key=KEY
    )


@pytest.mark.parametrize(
    "upstream", [httpx.Response(503), httpx.Response(429), httpx.ConnectError("x"), httpx.ReadTimeout("x")]
)
@pytest.mark.parametrize("provider,kind", sorted(POSTS))
async def test_paid_requests_are_sent_exactly_once(provider, kind, upstream):
    with respx.mock:
        route = _route("POST", POSTS[(provider, kind)], upstream)
        with pytest.raises(ProviderError):
            await _post(provider, kind)
    assert route.call_count == 1
