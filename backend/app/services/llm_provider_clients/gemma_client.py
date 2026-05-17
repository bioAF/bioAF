"""Gemma 4 self-hosted provider client (ADR-054).

Unlike the hosted clients, Gemma does not synchronously return a response. The
client's `submit` dispatches an inference pipeline through the existing
Nextflow / GKE orchestration, which provisions an L4 GPU GCE instance per
request, runs the model, writes the response text to GCS, and tears the
instance down. The pipeline monitor wakes the job back up when the pipeline
reaches a terminal state.

`list_models` returns the static fallback list from llm_provider_models.py
since Gemma has no introspection endpoint of its own.
"""

from __future__ import annotations

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_models import FALLBACK_MODELS


async def list_models(api_key: str | None) -> list[str]:
    return list(FALLBACK_MODELS["gemma"])


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    """Synchronous submit is not supported for Gemma.

    The job service detects provider=gemma and dispatches execute_gemma instead
    of execute_hosted, so this code path should never be reached. Raise to make
    accidental misuse loud.
    """
    raise ProviderError(
        "Gemma 4 inference runs through the pipeline orchestrator, not a synchronous call. "
        "Use agent_review_job_service.execute_gemma.",
        error_class="other",
    )
