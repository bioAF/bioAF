"""plan_6 step 8: in autonomous mode the model ratifies the verdict.

`classify_study` does not move. Deterministic code MEASURES: it still computes the suggested verdict
from the numbers, auditably, and a human can still read exactly why a study landed where it did.
What changes in autonomous mode is who gets the last word on that suggestion.

In assisted mode nothing changes at all: a clean `validated` auto-finalises and everything else
holds at `comparing` for a person, which is the shipped policy.
"""

import pytest

from app.services import validation_ratification as rat

_COMPARISONS = [
    {"metric_key": "peak_count", "mapped_key": "peak_count", "verdict": "agree", "advisory": False},
    {"metric_key": "alignment_rate", "mapped_key": "reads_mapped_genome", "verdict": "diverge", "advisory": False},
]
_RESULT = {
    "classification": "inconclusive",
    "auto_finalize": False,
    "reasoning": "one finding agreed and one QC floor diverged",
    "comparisons": _COMPARISONS,
    "attribution": {"our_side": "suspected", "reasons": ["pipeline mapping confidence is 'partial'"]},
    "coverage": {"targets": 2, "comparable": 2, "agree": 1, "diverge": 1, "finding_agree": 1},
}


def _response(action, verdict=None, reasoning="because", evidence=None):
    import json

    body = {"action": action, "verdict": verdict, "reasoning": reasoning}
    if evidence is not None:
        body["evidence_reweighed"] = evidence
    return "```json\n" + json.dumps(body) + "\n```"


class TestThePrompt:
    def test_it_shows_the_measurements_and_the_suggested_verdict(self):
        system, payload = rat.build_ratification_prompt(_RESULT)
        assert "inconclusive" in payload
        assert "peak_count" in payload
        assert "accept" in system.lower() and "override" in system.lower()

    def test_it_requires_an_override_to_name_the_evidence_it_reweighs(self):
        system, _ = rat.build_ratification_prompt(_RESULT)
        assert "evidence_reweighed" in system

    def test_it_says_the_measurements_are_not_up_for_debate(self):
        """The model ratifies the VERDICT. It does not get to dispute the computed numbers, which
        are what make the result auditable."""
        system, _ = rat.build_ratification_prompt(_RESULT)
        assert "measurement" in system.lower()


class TestParsing:
    def test_an_accept_keeps_the_suggested_verdict(self):
        out = rat.parse_ratification(_response("accept"), suggested="inconclusive")
        assert out["action"] == "accept"
        assert out["verdict"] == "inconclusive"

    def test_an_override_carries_its_new_verdict_and_reasoning(self):
        out = rat.parse_ratification(
            _response("override", "not_validated", "the divergence is the paper's", ["alignment_rate"]),
            suggested="inconclusive",
        )
        assert out["action"] == "override"
        assert out["verdict"] == "not_validated"
        assert out["reasoning"] == "the divergence is the paper's"
        assert out["evidence_reweighed"] == ["alignment_rate"]

    def test_an_override_naming_no_evidence_is_refused(self):
        """An override that reweighs nothing is an assertion, not a judgment. It falls back to the
        measured verdict rather than silently replacing it."""
        out = rat.parse_ratification(_response("override", "validated", "trust me", []), suggested="inconclusive")
        assert out["action"] == "accept"
        assert out["verdict"] == "inconclusive"
        assert "evidence" in out["reasoning"].lower()

    def test_an_override_to_a_verdict_that_does_not_exist_is_refused(self):
        out = rat.parse_ratification(_response("override", "brilliant", "r", ["peak_count"]), suggested="inconclusive")
        assert out["action"] == "accept"
        assert out["verdict"] == "inconclusive"

    def test_junk_falls_back_to_the_measured_verdict(self):
        out = rat.parse_ratification("the model said nothing useful", suggested="inconclusive")
        assert out["action"] == "accept"
        assert out["verdict"] == "inconclusive"


class TestRatify:
    @pytest.mark.asyncio
    async def test_assisted_does_not_call_the_model_at_all(self):
        class _Boom:
            async def submit(self, **kwargs):
                raise AssertionError("assisted mode must not ask the model to ratify")

        out = await rat.ratify(_RESULT, autonomy="assisted", client=_Boom(), model="m", api_key=None)
        assert out is None

    @pytest.mark.asyncio
    async def test_autonomous_accepts_a_clean_validated_and_finalises(self):
        clean = dict(_RESULT, classification="validated", auto_finalize=True)

        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return _response("accept")

        out = await rat.ratify(clean, autonomy="autonomous", client=_C(), model="claude-opus-4-8", api_key=None)
        assert out["verdict"] == "validated"
        assert out["ratified_by"] == "model"
        assert out["ratified_by_model"] == "claude-opus-4-8"
        assert out["suggested_verdict"] == "validated"
        assert out["finalize"] is True

    @pytest.mark.asyncio
    async def test_autonomous_overrides_an_inconclusive_and_stores_its_reasoning(self):
        class _C:
            async def submit(self, prompt, payload, model, api_key, attachments=None):
                return _response(
                    "override", "not_validated", "the alignment divergence is the paper's", ["alignment_rate"]
                )

        out = await rat.ratify(_RESULT, autonomy="autonomous", client=_C(), model="m", api_key=None)
        assert out["verdict"] == "not_validated"
        assert out["suggested_verdict"] == "inconclusive"
        assert out["reasoning"] == "the alignment divergence is the paper's"
        assert out["evidence_reweighed"] == ["alignment_rate"]
        assert out["finalize"] is True

    @pytest.mark.asyncio
    async def test_a_provider_failure_leaves_the_measured_verdict_standing(self):
        """The model failing to answer must not finalise a study on a guess, and must not lose the
        measurement either."""

        class _C:
            async def submit(self, **kwargs):
                raise RuntimeError("provider exploded")

        out = await rat.ratify(_RESULT, autonomy="autonomous", client=_C(), model="m", api_key=None)
        assert out is None


# ---- the driver: what each mode actually does at `comparing` ----


import pytest_asyncio  # noqa: E402

from app.services.pipeline_run_service import PipelineRunService  # noqa: E402
from app.services.reproduction_plan_service import ReproductionPlanService  # noqa: E402
from app.services.validation_driver_service import ValidationDriverService  # noqa: E402
from app.services.validation_study_service import ValidationStudyService  # noqa: E402

_CLEAN_VALIDATED = {
    "computed_metrics": {"peak_count": 25_000, "reads_mapped_genome": 0.834},
    "comparison_targets": [
        {"metric_key": "num_peaks", "claimed_value": 24_000, "unit": None, "tolerance": None},
        {"metric_key": "alignment_rate", "claimed_value": 83.4, "unit": "%", "tolerance": None},
    ],
}
_DIVERGENT = {
    "computed_metrics": {"cell_count": 2000},
    "comparison_targets": [{"metric_key": "cell_count", "claimed_value": 10000, "unit": None, "tolerance": None}],
}


async def _study_at_comparing(session, user, evidence, *, mapping_confidence=None):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_doi="10.1/abc")
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["SRR390728"],
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        reference_genome="GRCh38",
    )
    if mapping_confidence:
        plan.mapping_confidence = mapping_confidence
    study.state = "comparing"
    study.evidence_json = evidence
    await session.flush()
    return study


@pytest_asyncio.fixture
async def _no_launch(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(PipelineRunService, "launch_run", _noop)


def _patch_ratifier(monkeypatch, response, model="claude-opus-4-8"):
    from types import SimpleNamespace

    from app.services import validation_driver_service as drv

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model=model, api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(drv, "get_client", lambda p: _C())


async def _set_autonomy(session, org_id, mode):
    from sqlalchemy import select

    from app.models.organization import Organization

    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    org.lit_validation_autonomy = mode
    await session.flush()


class TestAssistedIsUnchanged:
    @pytest.mark.asyncio
    async def test_a_clean_validated_still_auto_finalises(self, session, admin_user, _no_launch):
        study = await _study_at_comparing(session, admin_user, _CLEAN_VALIDATED)
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "classified"
        assert study.classification == "validated"
        assert study.evidence_json.get("ratification") is None

    @pytest.mark.asyncio
    async def test_a_divergence_still_holds_at_comparing(self, session, admin_user, _no_launch):
        study = await _study_at_comparing(session, admin_user, _DIVERGENT, mapping_confidence="partial")
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "comparing"
        assert study.classification is None


class TestAutonomousRatifies:
    @pytest.mark.asyncio
    async def test_it_accepts_a_clean_validated_and_finalises(self, session, admin_user, monkeypatch, _no_launch):
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_ratifier(monkeypatch, _response("accept", reasoning="the finding reproduced"))
        study = await _study_at_comparing(session, admin_user, _CLEAN_VALIDATED)

        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "classified"
        assert study.classification == "validated"
        ratification = study.evidence_json["ratification"]
        assert ratification["ratified_by"] == "model"
        assert ratification["ratified_by_model"] == "claude-opus-4-8"
        assert ratification["suggested_verdict"] == "validated"

    @pytest.mark.asyncio
    async def test_it_overrides_an_inconclusive_and_stores_its_reasoning(
        self, session, admin_user, monkeypatch, _no_launch
    ):
        """The study finalises on the model's answer, and the record says the measurement suggested
        something else and which evidence moved it."""
        await _set_autonomy(session, admin_user.organization_id, "autonomous")
        _patch_ratifier(
            monkeypatch,
            _response("override", "not_validated", "the cell-count shortfall is the paper's", ["cell_count"]),
        )
        study = await _study_at_comparing(session, admin_user, _DIVERGENT, mapping_confidence="partial")

        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "classified"
        assert study.classification == "not_validated"
        ratification = study.evidence_json["ratification"]
        assert ratification["suggested_verdict"] == "inconclusive"
        assert ratification["reasoning"] == "the cell-count shortfall is the paper's"
        assert ratification["evidence_reweighed"] == ["cell_count"]
        # The measurement is still on the record beside the override.
        assert study.evidence_json["classification_result"]["classification"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_a_ratification_outage_holds_the_study_for_a_person(
        self, session, admin_user, monkeypatch, _no_launch
    ):
        """A provider outage must not finalise on a guess. The study falls back to the assisted
        behaviour, which is the safe one."""
        from app.services import validation_driver_service as drv

        await _set_autonomy(session, admin_user.organization_id, "autonomous")

        class _Boom:
            async def submit(self, **kwargs):
                raise RuntimeError("provider exploded")

        from types import SimpleNamespace

        async def fake_get_for_feature(sess, org_id, feature):
            return SimpleNamespace(provider="anthropic", model="m", api_key=None)

        monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
        monkeypatch.setattr(drv, "get_client", lambda p: _Boom())
        study = await _study_at_comparing(session, admin_user, _DIVERGENT, mapping_confidence="partial")

        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)

        assert study.state == "comparing"
        assert study.classification is None
