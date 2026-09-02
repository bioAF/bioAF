"""plan_6 step 6: per-feature model selection.

Literature validation and literature review are different jobs with different demands, and an org
should not have to pick one model for both. Two independent overrides, each naming a provider that
must ALREADY be configured, so the API key still comes from that provider's own row and no secret is
stored twice.
"""

import pytest

from app.models.llm_provider_config import LlmProviderConfig
from app.services import llm_provider_config_service as svc
from app.services.llm_feature_models import FEATURE_LITERATURE_REVIEW, FEATURE_LITERATURE_VALIDATION, VALID_FEATURES


async def _config(session, org_id, user_id, provider, model, active=False, key="k"):
    cfg = LlmProviderConfig(
        organization_id=org_id,
        provider=provider,
        api_key=key,
        model=model,
        is_active=active,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    session.add(cfg)
    await session.flush()
    return cfg


class TestResolvingAFeaturesModel:
    @pytest.mark.asyncio
    async def test_with_no_override_it_is_the_org_active_config(self, session, admin_user):
        """Today's behaviour, exactly. Nothing changes for an org that never sets an override."""
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)

        cfg = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_VALIDATION)
        assert cfg is not None
        assert (cfg.provider, cfg.model) == ("anthropic", "claude-opus-4-8")

    @pytest.mark.asyncio
    async def test_an_override_substitutes_its_model_and_keeps_the_providers_key(self, session, admin_user):
        """The override names a model, not a secret. The key comes from the provider's own row, so
        rotating a key in one place keeps working."""
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro", key="google-key")
        await svc.set_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", "gemini-3-pro"
        )

        cfg = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_VALIDATION)
        assert (cfg.provider, cfg.model) == ("google", "gemini-3-pro")
        assert cfg.api_key == "google-key"

    @pytest.mark.asyncio
    async def test_the_two_features_are_independent(self, session, admin_user):
        """Overriding validation must not move lit review. They are separate decisions."""
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro")
        await svc.set_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", "gemini-3-pro"
        )

        validation = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_VALIDATION)
        review = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_REVIEW)
        assert validation.provider == "google"
        assert (review.provider, review.model) == ("anthropic", "claude-opus-4-8")

    @pytest.mark.asyncio
    async def test_an_override_is_replaced_not_duplicated(self, session, admin_user):
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro")
        for model in ("gemini-3-pro", "gemini-3-flash"):
            await svc.set_feature_override(
                session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", model
            )

        cfg = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_VALIDATION)
        assert cfg.model == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_clearing_an_override_returns_to_the_active_config(self, session, admin_user):
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro")
        await svc.set_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", "gemini-3-pro"
        )
        await svc.clear_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION
        )

        cfg = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_VALIDATION)
        assert cfg.provider == "anthropic"


class TestRefusingAnUnusableOverride:
    @pytest.mark.asyncio
    async def test_an_unconfigured_provider_is_refused_and_named(self, session, admin_user):
        """Saved silently, this would fail at extraction time with no key and no explanation. The
        message has to say which provider needs one."""
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)

        with pytest.raises(Exception) as exc:
            await svc.set_feature_override(
                session,
                admin_user.organization_id,
                admin_user.id,
                FEATURE_LITERATURE_VALIDATION,
                "google",
                "gemini-3-pro",
            )
        assert "google" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_a_provider_configured_without_a_key_is_refused(self, session, admin_user):
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro", key=None)

        with pytest.raises(Exception) as exc:
            await svc.set_feature_override(
                session,
                admin_user.organization_id,
                admin_user.id,
                FEATURE_LITERATURE_VALIDATION,
                "google",
                "gemini-3-pro",
            )
        assert "google" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_a_feature_that_does_not_exist_is_refused(self, session, admin_user):
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)

        with pytest.raises(Exception):
            await svc.set_feature_override(
                session, admin_user.organization_id, admin_user.id, "telepathy", "anthropic", "claude-opus-4-8"
            )

    def test_the_two_features_are_the_whole_vocabulary(self):
        assert VALID_FEATURES == (FEATURE_LITERATURE_VALIDATION, FEATURE_LITERATURE_REVIEW)


class TestTheFeaturesUseTheirOwnModel:
    """The resolver is only worth anything if the two features actually call it."""

    @pytest.mark.asyncio
    async def test_extraction_runs_on_the_validation_override(self, session, admin_user, monkeypatch):
        from app.services import validation_extraction_service as ext
        from app.services.validation_study_service import ValidationStudyService

        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro", key="gk")
        await svc.set_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", "gemini-3-pro"
        )
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()

        seen = {}

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                seen["model"], seen["api_key"] = model, api_key
                return '```json\n{"accessions": [], "claims": [], "blockers": []}\n```'

        monkeypatch.setattr(ext, "get_client", lambda p: _C())

        plan = await ext.ValidationExtractionService.extract(
            session, study, "TEXT", admin_user.organization_id, admin_user.id
        )
        await session.commit()

        assert seen["model"] == "gemini-3-pro"
        assert seen["api_key"] == "gk"
        # And the plan records which model actually read the paper, not the org default.
        assert plan.extractor_model == "gemini-3-pro"
        assert plan.extractor_provider == "google"

    @pytest.mark.asyncio
    async def test_extraction_is_unchanged_with_no_override(self, session, admin_user, monkeypatch):
        from app.services import validation_extraction_service as ext
        from app.services.validation_study_service import ValidationStudyService

        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        await session.flush()

        seen = {}

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                seen["model"] = model
                return '```json\n{"accessions": [], "claims": [], "blockers": []}\n```'

        monkeypatch.setattr(ext, "get_client", lambda p: _C())
        await ext.ValidationExtractionService.extract(session, study, "TEXT", admin_user.organization_id, admin_user.id)
        await session.commit()

        assert seen["model"] == "claude-opus-4-8"

    @pytest.mark.asyncio
    async def test_a_validation_override_does_not_move_lit_review(self, session, admin_user):
        """The failure this guards against is one override quietly changing both features."""
        await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-opus-4-8", active=True)
        await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro")
        await svc.set_feature_override(
            session, admin_user.organization_id, admin_user.id, FEATURE_LITERATURE_VALIDATION, "google", "gemini-3-pro"
        )

        review = await svc.get_for_feature(session, admin_user.organization_id, FEATURE_LITERATURE_REVIEW)
        assert (review.provider, review.model) == ("anthropic", "claude-opus-4-8")
