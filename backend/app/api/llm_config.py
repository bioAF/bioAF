"""LLM provider config API (ADR-053).

Admin-only endpoints for the Settings > Integrations > LLMs page. All routes
gated by llm_integration:configure; non-admins receive 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.exceptions import ValidationError
from app.services import llm_provider_config_service
from app.services.llm_feature_models import VALID_FEATURES
from app.services.llm_suitability import suitability_for
from app.services.llm_models_fetch_service import list_models_with_fallback
from app.services.llm_provider_clients import ProviderError, get_client
from app.services.llm_provider_config_service import (
    HOSTED_PROVIDERS,
    SUPPORTED_PROVIDERS,
)

router = APIRouter(prefix="/api/integrations/llm", tags=["llm-config"])


class ProviderConfigSummary(BaseModel):
    provider: str
    model: str | None
    api_key_prefix_last5: str | None
    is_active: bool
    configured: bool


class ProviderModelList(BaseModel):
    provider: str
    models: list[str]
    used_fallback: bool


class ProvidersResponse(BaseModel):
    configs: list[ProviderConfigSummary]
    active_provider: str | None
    model_lists: list[ProviderModelList]


class UpsertProviderRequest(BaseModel):
    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)


class FeatureModelSummary(BaseModel):
    feature: str
    provider: str | None
    model: str | None
    # Whether this feature names its own model, or is simply running on the org's active provider.
    # The UI has to be able to say which, because "clear the override" is only meaningful for one.
    overridden: bool
    suitability: dict


class FeatureModelsResponse(BaseModel):
    features: list[FeatureModelSummary]


class SetFeatureModelRequest(BaseModel):
    provider: str
    model: str


class TestProviderResponse(BaseModel):
    ok: bool
    provider: str
    model_count: int | None = None
    error: str | None = None
    error_class: str | None = None


@router.get("/providers", response_model=ProvidersResponse)
async def list_providers(
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    configs = await llm_provider_config_service.list_for_org(session, org_id)
    by_provider = {c.provider: c for c in configs}

    summaries = []
    for provider in sorted(SUPPORTED_PROVIDERS):
        c = by_provider.get(provider)
        summaries.append(
            ProviderConfigSummary(
                provider=provider,
                model=c.model if c else None,
                api_key_prefix_last5=c.api_key_prefix_last5 if c else None,
                is_active=bool(c and c.is_active),
                configured=c is not None,
            )
        )

    active = next((c.provider for c in configs if c.is_active), None)

    model_lists = []
    for provider in sorted(SUPPORTED_PROVIDERS):
        c = by_provider.get(provider)
        api_key = c.api_key if c else None
        models, used_fallback = await list_models_with_fallback(provider, api_key)
        model_lists.append(ProviderModelList(provider=provider, models=models, used_fallback=used_fallback))

    return ProvidersResponse(configs=summaries, active_provider=active, model_lists=model_lists)


@router.post("/providers/deactivate", status_code=204)
async def deactivate_all(
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    await llm_provider_config_service.deactivate_all(session, org_id=org_id, actor_user_id=user_id)
    await session.commit()


@router.post("/providers/{provider}/test", response_model=TestProviderResponse)
async def test_provider(
    provider: str,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Hit the provider's /models endpoint with the stored key.

    A cheap end-to-end check that the key authenticates and the network path
    is reachable. Returns model_count on success; on failure returns the
    verbatim provider error so the admin can act on it (revoked key,
    rate-limit, unreachable host, etc.).
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    org_id = int(current_user["org_id"])

    cfg = await llm_provider_config_service.get_for_provider(session, org_id, provider)
    if cfg is None:
        return TestProviderResponse(
            ok=False,
            provider=provider,
            error="No configuration saved for this provider.",
            error_class="not_configured",
        )

    api_key = cfg.api_key
    if provider in HOSTED_PROVIDERS and not api_key:
        return TestProviderResponse(
            ok=False,
            provider=provider,
            error="No API key saved for this provider.",
            error_class="not_configured",
        )

    client = get_client(provider)
    try:
        models = await client.list_models(api_key)
    except ProviderError as exc:
        return TestProviderResponse(
            ok=False,
            provider=provider,
            error=str(exc)[:2000],
            error_class=exc.error_class,
        )
    return TestProviderResponse(ok=True, provider=provider, model_count=len(models))


@router.post("/providers/{provider}", response_model=ProviderConfigSummary)
async def upsert_provider(
    provider: str,
    body: UpsertProviderRequest,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    if provider in HOSTED_PROVIDERS and not body.api_key:
        # Allow saving model-only on update when a key already exists.
        existing = await llm_provider_config_service.get_for_provider(session, org_id, provider)
        if existing is None or not existing.api_key:
            raise HTTPException(400, f"provider {provider} requires an api_key")
        api_key = existing.api_key
    else:
        api_key = body.api_key

    row = await llm_provider_config_service.upsert(
        session,
        org_id=org_id,
        provider=provider,
        api_key=api_key,
        model=body.model,
        actor_user_id=user_id,
    )
    await session.commit()

    return ProviderConfigSummary(
        provider=row.provider,
        model=row.model,
        api_key_prefix_last5=row.api_key_prefix_last5,
        is_active=row.is_active,
        configured=True,
    )


@router.post("/providers/{provider}/activate", response_model=ProviderConfigSummary)
async def activate_provider(
    provider: str,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    row = await llm_provider_config_service.set_active(session, org_id=org_id, provider=provider, actor_user_id=user_id)
    await session.commit()

    return ProviderConfigSummary(
        provider=row.provider,
        model=row.model,
        api_key_prefix_last5=row.api_key_prefix_last5,
        is_active=row.is_active,
        configured=True,
    )


@router.delete("/providers/{provider}", status_code=204)
async def delete_provider(
    provider: str,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider}")
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    await llm_provider_config_service.delete(session, org_id=org_id, provider=provider, actor_user_id=user_id)
    await session.commit()


async def _feature_summary(session: AsyncSession, org_id: int, feature: str) -> FeatureModelSummary:
    overrides = {o.feature for o in await llm_provider_config_service.list_feature_overrides(session, org_id)}
    cfg = await llm_provider_config_service.get_for_feature(session, org_id, feature)
    provider = cfg.provider if cfg else None
    model = cfg.model if cfg else None
    return FeatureModelSummary(
        feature=feature,
        provider=provider,
        model=model,
        overridden=feature in overrides,
        # The verdict is for the model actually in use, override or not: an org running an unsuitable
        # model as its ORG default has the same problem as one that chose it for this feature.
        suitability=suitability_for(provider, model),
    )


@router.get("/feature-models", response_model=FeatureModelsResponse)
async def list_feature_models(
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    return FeatureModelsResponse(
        features=[await _feature_summary(session, org_id, feature) for feature in VALID_FEATURES]
    )


@router.put("/feature-models/{feature}", response_model=FeatureModelSummary)
async def set_feature_model(
    feature: str,
    body: SetFeatureModelRequest,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        await llm_provider_config_service.set_feature_override(
            session, org_id, user_id, feature, body.provider, body.model
        )
    except ValidationError as exc:
        # A suitability warning never lands here: it informs and the save proceeds. This is the
        # different case of an override that could not work at all.
        raise HTTPException(400, str(exc))
    await session.commit()
    return await _feature_summary(session, org_id, feature)


@router.delete("/feature-models/{feature}", status_code=204)
async def clear_feature_model(
    feature: str,
    current_user: dict = require_permission("llm_integration", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    if feature not in VALID_FEATURES:
        raise HTTPException(400, f"feature must be one of {VALID_FEATURES}")
    await llm_provider_config_service.clear_feature_override(session, org_id, user_id, feature)
    await session.commit()
