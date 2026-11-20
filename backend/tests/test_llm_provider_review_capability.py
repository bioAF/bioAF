"""Review-execution capability of the registered LLM providers.

Behaviors verified:
- The hosted providers (OpenAI, Anthropic, Google) have an implemented review
  execution path.
- Self-hosted Gemma does not: no executor exists, so a review it accepted could
  never run.
- An unknown provider fails closed.
- Review execution is a separate capability from tool calling.
"""

from __future__ import annotations

import pytest

from app.services.llm_provider_clients import supports_review_execution, supports_tools


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_hosted_providers_support_review_execution(provider):
    assert supports_review_execution(provider) is True


def test_gemma_does_not_support_review_execution():
    assert supports_review_execution("gemma") is False


def test_unknown_provider_fails_closed():
    assert supports_review_execution("not-a-provider") is False


def test_review_execution_is_distinct_from_tool_calling(monkeypatch):
    from app.services.llm_provider_clients import gemma_client

    # A provider gaining tool calling must not imply it can run reviews.
    monkeypatch.setattr(gemma_client, "SUPPORTS_TOOLS", True, raising=False)
    assert supports_tools("gemma") is True
    assert supports_review_execution("gemma") is False
