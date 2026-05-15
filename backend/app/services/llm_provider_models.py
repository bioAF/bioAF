"""Hardcoded fallback model lists per LLM provider (ADR-053).

Used when the live fetch from a provider's /models endpoint fails (provider
down, key invalid, network error). The list is global to the deployment, not
per-org; updating it is a release activity.
"""

FALLBACK_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-4.1",
        "gpt-4o",
    ],
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "google": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    "gemma": [
        "gemma-4-9b",
        "gemma-4-2b",
    ],
}
