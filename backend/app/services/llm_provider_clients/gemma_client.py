"""Gemma 4 self-hosted provider client (ADR-054).

Gemma can be configured as a provider and lists its static models, but it has no
review execution path: no inference pipeline, executor, or completion hook exists
yet. SUPPORTS_REVIEW_EXECUTION is therefore False, so review admission refuses
Gemma before any job is created, availability reports Agent Review disabled, and
startup recovery fails any Gemma job an earlier release accepted. Enabling it is a
separate implementation that must meet ADR-054 end to end; it must never fall back
to a hosted provider.

`list_models` returns the static fallback list from llm_provider_models.py
since Gemma has no introspection endpoint of its own.
"""

from __future__ import annotations

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_models import FALLBACK_MODELS

# Gemma has no native tool/function-calling path in v1, so the assistant gates it off
# (a JSON-protocol fallback loop is a later enhancement).
SUPPORTS_TOOLS = False

# No review executor exists (see module docstring); admission and availability fail closed.
SUPPORTS_REVIEW_EXECUTION = False


async def list_models(api_key: str | None) -> list[str]:
    return list(FALLBACK_MODELS["gemma"])


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    """Gemma review execution is not implemented.

    Review admission refuses providers without SUPPORTS_REVIEW_EXECUTION, so this is never
    reached through Agent Review. Raise to make accidental misuse loud.
    """
    raise ProviderError(
        "Gemma 4 review execution is not implemented.",
        error_class="other",
    )
