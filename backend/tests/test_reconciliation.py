"""change_7.1 section 6: inspected evidence has to reach the decisions that depend on it.

Study 32 discovered everything and revised nothing. Extraction ran before the supplement inventory
existed, so the claims were bound from prose alone; the production binding call never passed the
inventory argument the helper accepts; and blocked completion resolved the supplements without
touching the plan, the capability answers or the checks. The report ended up carrying newly
discovered facts beside stale interpretations of them.

**Retrieval is not reconciliation.** A test that hands already-correct targets to a storage helper
proves nothing about this. What must be true is that the reconciliation call RECEIVES the inspected
evidence and that its decisions land on the persisted state.

**A provisional reading is allowed to be wrong.** It becomes a defect only when better evidence
arrives and nothing revises it.
"""

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService

_S1 = {
    "label": "Supplemental File S1",
    "filename": "s1.txt",
    "role": "sample_metadata",
    "resolved": True,
    "row_count": 51,
    "columns": ["ProcessingID", "Sampletype", "Sex"],
}
_S3 = {
    "label": "Supplemental File S3",
    "filename": "s3.txt",
    "role": "results_table",
    "resolved": True,
    "row_count": 194,
    "columns": ["info", "log2FoldChange", "padj", "chr"],
    "threshold_splits": {"abs_log2fc>2": 88},
}


async def _targets(session, plan):
    """Loaded explicitly: the relationship is lazy and reading it directly attempts IO."""
    result = await session.execute(
        select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id).order_by(ComparisonTarget.id)
    )
    return list(result.scalars().all())


async def _study_with_provisional_plan(session, admin_user):
    """A study whose first reading got the context wrong, which is the normal case."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1101/gr.252981.119"
    )
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id)
    await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "differentially_expressed_genes",
                "claim_text": "194 genes were significant, 88 of them above a two-fold change",
                "claimed_value": 194,
                "unit": "genes",
            }
        ],
    )
    await session.flush()
    return study, plan


class TestTheReconciliationCallReceivesTheEvidence:
    @pytest.mark.asyncio
    async def test_the_inventory_reaches_the_binding_call(self, session, admin_user, monkeypatch):
        """The production caller never passed `inventory`. Adding an optional argument and not
        supplying it does not satisfy section 6."""
        from app.services import validation_reconciliation as rec

        seen: dict = {}

        async def _bind(claims, *, client, model, api_key, inventory=None, previous=None, on_issue=None):
            seen["inventory"] = inventory
            seen["claims"] = claims
            return []

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)

        await rec.reconcile(
            session,
            study,
            plan,
            supplements=[_S1, _S3],
            client=object(),
            model="m",
            api_key=None,
        )

        assert seen["inventory"], "the binding call was made without the inspected evidence"
        assert "Supplemental File S3" in seen["inventory"]
        assert "194" in seen["inventory"]

    @pytest.mark.asyncio
    async def test_the_claim_passage_travels_with_it(self, session, admin_user, monkeypatch):
        from app.services import validation_reconciliation as rec

        seen: dict = {}

        async def _bind(claims, *, client, model, api_key, inventory=None, previous=None, on_issue=None):
            seen["claims"] = claims
            return []

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)
        await rec.reconcile(session, study, plan, supplements=[_S1, _S3], client=object(), model="m", api_key=None)
        assert "88 of them above a two-fold change" in seen["claims"][0]["claim_text"]


class TestARevisionReachesThePersistedPlan:
    @pytest.mark.asyncio
    async def test_a_corrected_context_is_stored(self, session, admin_user):
        # change_7.3 section 7 (flagged test change, named by the plan): this stubbed `bind_claims`
        # to return the context fields, which hid that the binding schema never asked for them. It now
        # goes through the provider, so the real prompt, parser and application all run.
        from app.services import validation_reconciliation as rec

        provider = _Provider(
            '```json\n{"bindings": [{"claim_index": 0, "bound_key": "differentially_expressed_genes", '
            '"reason": "S3 holds 194 rows; the 88 is the fold-change subset, a separate claim", '
            '"confidence": 0.9, "threshold": 0.05, "threshold_kind": "padj", "sample_subset": "XX vs XY", '
            '"qc_stage": "post-QC"}]}\n```'
        )
        study, plan = await _study_with_provisional_plan(session, admin_user)
        await rec.reconcile(session, study, plan, supplements=[_S1, _S3], client=provider, model="m", api_key=None)
        target = (await _targets(session, plan))[0]
        assert target.threshold_kind == "padj"
        assert target.sample_subset == "XX vs XY"
        assert target.qc_stage == "post-QC"

    @pytest.mark.asyncio
    async def test_the_original_reading_and_the_reason_are_kept(self, session, admin_user, monkeypatch):
        """Section 6: preserve the initial interpretation and the reason for each revision."""
        from app.services import validation_reconciliation as rec

        async def _bind(claims, *, client, model, api_key, inventory=None, previous=None, on_issue=None):
            return [
                {
                    "claim_index": 0,
                    "bound_key": "differentially_expressed_genes",
                    "reason": "the 88 is a fold-change subset of the 194",
                    "confidence": 0.9,
                    "declined": False,
                    "threshold_kind": "padj",
                }
            ]

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)
        result = await rec.reconcile(
            session, study, plan, supplements=[_S1, _S3], client=object(), model="m", api_key=None
        )
        revision = result["revisions"][0]
        assert revision["before"]["threshold_kind"] is None
        assert revision["after"]["threshold_kind"] == "padj"
        assert "fold-change subset" in revision["reason"]

    @pytest.mark.asyncio
    async def test_a_failed_reconciliation_leaves_the_claim_unresolved_not_verified(
        self, session, admin_user, monkeypatch
    ):
        """Section 6: if reconciliation fails, retain the evidence and mark affected decisions
        unresolved. Do not treat the provisional answer as verified."""
        from app.services import validation_reconciliation as rec
        from app.services.validation_classifier_service import BINDING_FAILED

        async def _bind(claims, *, client, model, api_key, inventory=None, previous=None, on_issue=None):
            return [
                {
                    "claim_index": 0,
                    "bound_key": None,
                    "reason": "unreadable",
                    "confidence": 0.0,
                    "declined": False,
                    "bound_by": BINDING_FAILED,
                }
            ]

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)
        await rec.reconcile(session, study, plan, supplements=[_S1, _S3], client=object(), model="m", api_key=None)
        assert (await _targets(session, plan))[0].bound_by == BINDING_FAILED

    @pytest.mark.asyncio
    async def test_nothing_to_reconcile_against_makes_no_model_call(self, session, admin_user, monkeypatch):
        """No inspected evidence means no better answer is available, and spending a model call to
        re-ask the same question with the same inputs is waste."""
        from app.services import validation_reconciliation as rec

        called = []

        async def _bind(claims, **kw):
            called.append(1)
            return []

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)
        result = await rec.reconcile(session, study, plan, supplements=[], client=object(), model="m", api_key=None)
        assert called == []
        assert result["revisions"] == []


class TestItDoesNotRedoWorkOnRetry:
    @pytest.mark.asyncio
    async def test_a_second_pass_over_unchanged_evidence_is_skipped(self, session, admin_user, monkeypatch):
        """Section 6: persist progress so retries do not duplicate model calls."""
        from app.services import validation_reconciliation as rec

        calls = []

        async def _bind(claims, *, client, model, api_key, inventory=None, previous=None, on_issue=None):
            calls.append(inventory)
            return []

        monkeypatch.setattr(rec, "bind_claims", _bind)
        study, plan = await _study_with_provisional_plan(session, admin_user)
        for _ in range(2):
            await rec.reconcile(session, study, plan, supplements=[_S1, _S3], client=object(), model="m", api_key=None)
        assert len(calls) == 1


# ---- change_7.3 section 7 --------------------------------------------------------------------------


class _Provider:
    """The provider boundary, so the real `bind_claims` and its parser run."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.calls.append((prompt, payload))
        return self.answer


class TestTheBindingSchemaAsksForTheContext:
    """The binding response schema asked only for claim_index, bound_key, reason and confidence, so
    `qc_stage` and `threshold` landed only when a model volunteered them."""

    def test_every_context_field_is_requested(self):
        from app.services.validation_extraction_service import build_binding_prompt

        system, _ = build_binding_prompt([{"metric_key": "total_samples", "value": 54}])
        for field in ("sample_subset", "qc_stage", "direction", "threshold_kind", "output_type", "measurement_basis"):
            assert f'"{field}"' in system


class TestItRunsOnThePaperText:
    _PASSAGES = {
        "claims": [
            {
                "claim_text": "194 genes were significant, 88 of them above a two-fold change",
                "passage": "We identified 194 significantly differentially expressed genes. We further refined "
                "this list by selecting those with a log2 fold change >2 ... these 88 genes",
            }
        ],
        "statements": ["As a result, three TE biopsy samples were excluded for failing to pass quality control."],
    }

    @pytest.mark.asyncio
    async def test_zero_attachments_with_kept_passages_still_reconciles(self, session, admin_user):
        from app.services import validation_reconciliation as rec

        provider = _Provider('```json\n{"bindings": [{"claim_index": 0, "bound_key": null, "reason": "r"}]}\n```')
        study, plan = await _study_with_provisional_plan(session, admin_user)
        study.evidence_json = {"paper_passages": self._PASSAGES}
        result = await rec.reconcile(session, study, plan, supplements=[], client=provider, model="m", api_key=None)
        assert result["status"] == "performed"
        assert result["basis"] == "paper_text"
        assert provider.calls, "no model call was made on the paper's passages"

    @pytest.mark.asyncio
    async def test_the_call_receives_the_passage_and_the_exclusion_statement(self, session, admin_user):
        from app.services import validation_reconciliation as rec

        provider = _Provider('```json\n{"bindings": [{"claim_index": 0, "bound_key": null, "reason": "r"}]}\n```')
        study, plan = await _study_with_provisional_plan(session, admin_user)
        study.evidence_json = {"paper_passages": self._PASSAGES}
        await rec.reconcile(session, study, plan, supplements=[], client=provider, model="m", api_key=None)
        _, payload = provider.calls[0]
        assert "these 88 genes" in payload
        assert "three TE biopsy samples were excluded" in payload

    @pytest.mark.asyncio
    async def test_unchanged_passages_are_not_asked_again(self, session, admin_user):
        from app.services import validation_reconciliation as rec

        provider = _Provider('```json\n{"bindings": [{"claim_index": 0, "bound_key": null, "reason": "r"}]}\n```')
        study, plan = await _study_with_provisional_plan(session, admin_user)
        study.evidence_json = {"paper_passages": self._PASSAGES}
        for _ in range(2):
            await rec.reconcile(session, study, plan, supplements=[], client=provider, model="m", api_key=None)
        assert len(provider.calls) == 1
