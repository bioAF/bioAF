"""Per-provider client modules for the LLM provider abstraction (ADR-053).

Each provider exposes:
    async def list_models(api_key: str | None) -> list[str]
    async def submit(prompt: str, payload: str, model: str, api_key: str | None,
                     attachments: list[dict] | None = None,
                     max_tokens: int | None = None) -> ModelAnswer

ProviderError is the typed exception every client raises on auth, rate-limit,
network, or transport failures. Callers decide whether to fall back to the
hardcoded model list (fetch) or surface the error to the user (submit).
"""

from __future__ import annotations

from app.exceptions import ValidationError


class ModelAnswer(str):
    """A model's answer text, carrying what the provider reported about producing it.

    plan_8_1 section 1.1: a read has to record the output tokens it used and why the answer stopped,
    and the text alone cannot say. A ``str`` so every caller that reads the answer as text keeps
    working unchanged; ``None`` where the provider did not report a value.
    """

    output_tokens: int | None
    input_tokens: int | None
    stop_reason: str | None

    def __new__(
        cls,
        text: str,
        *,
        output_tokens: int | None = None,
        input_tokens: int | None = None,
        stop_reason: str | None = None,
    ) -> "ModelAnswer":
        answer = super().__new__(cls, text)
        answer.output_tokens = output_tokens
        answer.input_tokens = input_tokens
        answer.stop_reason = stop_reason
        return answer


class ProviderError(Exception):
    """Raised by provider clients on any non-2xx or transport failure.

    Carries the provider-supplied error text so the failed-card modal can show
    it verbatim. The error_class string is one of:
        auth, rate_limit, transport, server, parse, refusal, truncated, timeout, other.

    plan_8_1 section 1.2: a truncated answer keeps the text that arrived (``text``) and what it cost, so
    the cut-off answer can be kept for diagnosis. It is never parsed.
    """

    def __init__(
        self,
        message: str,
        *,
        error_class: str = "other",
        text: str | None = None,
        output_tokens: int | None = None,
        stop_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.text = text
        self.output_tokens = output_tokens
        self.stop_reason = stop_reason


def as_int(value) -> int | None:
    """A token count as the provider reported it, or None when it reported none."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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


def provider_of(client) -> str | None:
    """The provider a client object belongs to, or None for one this module does not own.

    plan_8_3 stage 6: a recovery budget is capped by what the provider's deadline allows, and the
    callers that ask for a decision hold the client, not the provider's name.
    """
    return next((name for name, module in CLIENTS.items() if module is client), None)
