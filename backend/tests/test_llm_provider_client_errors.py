"""Tests for provider-client transport error handling.

A `httpx.HTTPError` subclass like `ConnectError` or `ReadTimeout` often
stringifies to an empty string when raised without a message. The clients
must still surface something diagnostic in the `ProviderError` they raise,
otherwise the failed-review card renders a blank error field with no clue
to the root cause.

Each client's submit() and list_models() catches httpx.HTTPError and wraps
it in a transport-class ProviderError. This file pins the contract that the
wrapped message is never empty.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.llm_provider_clients import (
    ProviderError,
    anthropic_client,
    google_client,
    openai_client,
)


@pytest.mark.asyncio
async def test_openai_submit_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.post("/chat/completions").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await openai_client.submit(prompt="p", payload="x", model="gpt-x", api_key="sk")
    assert exc_info.value.error_class == "transport"
    detail = str(exc_info.value)
    assert detail.strip() != ""
    assert "ConnectError" in detail


@pytest.mark.asyncio
async def test_anthropic_submit_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.post("/messages").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await anthropic_client.submit(prompt="p", payload="x", model="claude-x", api_key="sk-ant")
    assert exc_info.value.error_class == "transport"
    detail = str(exc_info.value)
    assert detail.strip() != ""
    assert "ConnectError" in detail


@pytest.mark.asyncio
async def test_google_submit_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        r.post("/models/gemini-x:generateContent").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await google_client.submit(prompt="p", payload="x", model="gemini-x", api_key="g-key")
    assert exc_info.value.error_class == "transport"
    detail = str(exc_info.value)
    assert detail.strip() != ""
    assert "ConnectError" in detail


@pytest.mark.asyncio
async def test_openai_list_models_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://api.openai.com/v1") as r:
        r.get("/models").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await openai_client.list_models("sk")
    assert exc_info.value.error_class == "transport"
    assert "ConnectError" in str(exc_info.value)


@pytest.mark.asyncio
async def test_anthropic_list_models_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://api.anthropic.com/v1") as r:
        r.get("/models").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await anthropic_client.list_models("sk-ant")
    assert exc_info.value.error_class == "transport"
    assert "ConnectError" in str(exc_info.value)


@pytest.mark.asyncio
async def test_google_list_models_empty_transport_error_carries_class_name():
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as r:
        r.get("/models").mock(side_effect=httpx.ConnectError(""))
        with pytest.raises(ProviderError) as exc_info:
            await google_client.list_models("g-key")
    assert exc_info.value.error_class == "transport"
    assert "ConnectError" in str(exc_info.value)


# --- Read-timeout defaults ---------------------------------------------------
#
# Real production failure 2026-05-17: an experiment-scope Claude review with
# prompt+payload of ~17K chars consistently hit a `ReadTimeout('')` at exactly
# 60 seconds because every hosted client's `_TIMEOUT` defaulted to
# `httpx.Timeout(60.0, connect=10.0)`. Hosted LLM completions on richer prompts
# routinely take longer than that. These tests pin the floor: at least 180s
# read timeout, with the connect timeout staying short. Anyone refactoring the
# constants below this floor will trip the test.


def test_openai_client_read_timeout_is_at_least_180s():
    assert openai_client._TIMEOUT.read is not None and openai_client._TIMEOUT.read >= 180.0


def test_anthropic_client_read_timeout_is_at_least_180s():
    assert anthropic_client._TIMEOUT.read is not None and anthropic_client._TIMEOUT.read >= 180.0


def test_google_client_read_timeout_is_at_least_180s():
    assert google_client._TIMEOUT.read is not None and google_client._TIMEOUT.read >= 180.0
