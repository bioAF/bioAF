"""Tests for the model-list fetch with hardcoded fallback (ADR-053).

The contract:
    - On 200, the provider client returns the live list and used_fallback=False.
    - On any error (auth, server, transport, parse, rate limit), the service
      falls back to the hardcoded list and returns used_fallback=True.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.exceptions import ValidationError
from app.services.llm_models_fetch_service import list_models_with_fallback
from app.services.llm_provider_models import FALLBACK_MODELS


@pytest.mark.asyncio
async def test_openai_live_fetch_returns_live_list():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.get("/models").mock(
            return_value=Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-9999"},
                        {"id": "gpt-also-live"},
                    ]
                },
            )
        )
        models, used_fallback = await list_models_with_fallback("openai", "sk-test")
    assert models == ["gpt-9999", "gpt-also-live"]
    assert used_fallback is False


@pytest.mark.asyncio
async def test_openai_falls_back_on_auth_failure():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.get("/models").mock(return_value=Response(401, text="bad key"))
        models, used_fallback = await list_models_with_fallback("openai", "sk-bad")
    assert models == FALLBACK_MODELS["openai"]
    assert used_fallback is True


@pytest.mark.asyncio
async def test_openai_falls_back_on_server_error():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.get("/models").mock(return_value=Response(503, text="server down"))
        models, used_fallback = await list_models_with_fallback("openai", "sk-key")
    assert models == FALLBACK_MODELS["openai"]
    assert used_fallback is True


@pytest.mark.asyncio
async def test_anthropic_live_fetch():
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.get("/models").mock(
            return_value=Response(
                200,
                json={"data": [{"id": "claude-future-1"}]},
            )
        )
        models, used_fallback = await list_models_with_fallback("anthropic", "sk-ant-test")
    assert models == ["claude-future-1"]
    assert used_fallback is False


@pytest.mark.asyncio
async def test_google_live_fetch_strips_models_prefix():
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        r.get("/models").mock(
            return_value=Response(
                200,
                json={"models": [{"name": "models/gemini-x"}, {"name": "models/gemini-y"}]},
            )
        )
        models, used_fallback = await list_models_with_fallback("google", "ai-key")
    assert models == ["gemini-x", "gemini-y"]
    assert used_fallback is False


@pytest.mark.asyncio
async def test_gemma_returns_static_list_with_no_egress():
    models, used_fallback = await list_models_with_fallback("gemma", None)
    assert models == FALLBACK_MODELS["gemma"]
    # Gemma returns whatever its client list_models reports; that is the
    # fallback list itself, so used_fallback is the contract of "no live
    # network egress happened" which is False in this case.
    assert used_fallback is False


@pytest.mark.asyncio
async def test_unknown_provider_raises():
    with pytest.raises(ValidationError):
        await list_models_with_fallback("unknown", "key")
