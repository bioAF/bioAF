"""Fetch the available models for a provider, with hardcoded fallback (ADR-053).

Returns (models, used_fallback). used_fallback is True whenever the live
provider call failed for any reason. The fallback list lives in
llm_provider_models.py.
"""

from __future__ import annotations

from app.services.llm_provider_clients import ProviderError, get_client
from app.services.llm_provider_models import FALLBACK_MODELS


async def list_models_with_fallback(provider: str, api_key: str | None) -> tuple[list[str], bool]:
    client = get_client(provider)
    try:
        models = await client.list_models(api_key)
        if not models:
            return list(FALLBACK_MODELS.get(provider, [])), True
        return models, False
    except ProviderError:
        return list(FALLBACK_MODELS.get(provider, [])), True
