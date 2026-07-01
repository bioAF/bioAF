"""Per-provider client modules for the LLM provider abstraction (ADR-053).

Each provider exposes:
    async def list_models(api_key: str | None) -> list[str]
    async def submit(prompt: str, payload: str, model: str, api_key: str | None,
                     attachments: list[dict] | None = None) -> str

ProviderError is the typed exception every client raises on auth, rate-limit,
network, or transport failures. Callers decide whether to fall back to the
hardcoded model list (fetch) or surface the error to the user (submit).
"""

from __future__ import annotations

from app.exceptions import ValidationError


class ProviderError(Exception):
    """Raised by provider clients on any non-2xx or transport failure.

    Carries the provider-supplied error text so the failed-card modal can show
    it verbatim. The error_class string is one of:
        auth, rate_limit, transport, server, parse, other.
    """

    def __init__(self, message: str, *, error_class: str = "other") -> None:
        super().__init__(message)
        self.error_class = error_class


from app.services.llm_provider_clients import (  # noqa: E402
    anthropic_client,
    gemma_client,
    google_client,
    openai_client,
)

CLIENTS = {
    "openai": openai_client,
    "anthropic": anthropic_client,
    "google": google_client,
    "gemma": gemma_client,
}


def get_client(provider: str):
    if provider not in CLIENTS:
        raise ValidationError(f"unknown provider: {provider}")
    return CLIENTS[provider]


def supports_tools(provider: str) -> bool:
    """Whether a provider exposes native tool/function calling (the assistant, L4).

    Unknown providers default to False so the assistant fails closed (unavailable) rather
    than attempting a tool-calling request a client cannot make."""
    client = CLIENTS.get(provider)
    return bool(getattr(client, "SUPPORTS_TOOLS", False))
