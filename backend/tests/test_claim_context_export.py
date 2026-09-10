"""change_7.2 section 5: report what is already recorded, and say plainly what was not attempted.

Claim context IS being stored. All eight targets on study 33's plan carry `sample_subset`,
`threshold`, `threshold_kind` and `output_type` at confidences between 0.9 and 0.96. None of it
reached the export: the serializer emitted `metric_key`, `claimed_value`, `unit`, `tolerance` and
`source_locator` and dropped every column migrations 134 and 135 added, plus every binding
attribution field plan_6 added.

Serialization reveals recorded context. It does not correct recorded context that is wrong, and it
does not turn an inspected supplement into validation of a claim.
"""

import pytest

from app.models.comparison_target import ComparisonTarget
from app.services.provenance.data_gatherer import ProvenanceDataGatherer
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService


async def _study_with_a_recorded_claim(session, admin_user):
    """A target as it exists on a study recorded BEFORE this change: every column populated by the
    extraction, and nothing in the export."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1101/gr.252981.119"
    )
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, accessions=["EGAS00001003667"], pipeline_key="nf-core/rnaseq"
    )
    study.reproduction_plan_id = plan.id
    session.add(
        ComparisonTarget(
            reproduction_plan_id=plan.id,
            metric_key="differentially_expressed_genes",
            claim_text="88 of the 194 significant genes had |log2FC| > 2",
            claimed_value=88,
            unit="genes",
            tolerance=0.1,
            source_locator="Results",
            sample_subset="XX vs XY",
            qc_stage="post-QC",
            direction="up",
            threshold=2.0,
            threshold_kind="abs_log2fc",
            output_type="gene_list",
            measurement_basis="sample",
            bound_key=None,
            binding_reason="S3 holds 194 rows; the 88 is the fold-change subset",
            binding_confidence=0.94,
            bound_by_model="claude-opus-4-8",
            bound_by="model",
        )
    )
    await session.commit()
    return study


class TestTheRecordedContextReachesTheExport:
    @pytest.mark.asyncio
    async def test_a_study_recorded_before_this_change_exports_its_claim_context(self, session, admin_user):
        study = await _study_with_a_recorded_claim(session, admin_user)
        data = await ProvenanceDataGatherer.gather_validation_study(session, study.id, admin_user.organization_id)
        target = data.comparison_targets[0]
        assert target["sample_subset"] == "XX vs XY"
        assert target["threshold"] == 2.0
        assert target["threshold_kind"] == "abs_log2fc"
        assert target["output_type"] == "gene_list"

    @pytest.mark.asyncio
    async def test_it_exports_the_measurement_basis(self, session, admin_user):
        study = await _study_with_a_recorded_claim(session, admin_user)
        data = await ProvenanceDataGatherer.gather_validation_study(session, study.id, admin_user.organization_id)
        assert data.comparison_targets[0]["measurement_basis"] == "sample"

    @pytest.mark.asyncio
    async def test_it_exports_the_binding_attribution(self, session, admin_user):
        """An AI decision that cannot be attributed is a defect."""
        study = await _study_with_a_recorded_claim(session, admin_user)
        data = await ProvenanceDataGatherer.gather_validation_study(session, study.id, admin_user.organization_id)
        target = data.comparison_targets[0]
        assert target["bound_by"] == "model"
        assert target["bound_by_model"] == "claude-opus-4-8"
        assert target["binding_confidence"] == 0.94
        assert "194 rows" in target["binding_reason"]

    @pytest.mark.asyncio
    async def test_it_exports_the_paper_s_own_wording(self, session, admin_user):
        study = await _study_with_a_recorded_claim(session, admin_user)
        data = await ProvenanceDataGatherer.gather_validation_study(session, study.id, admin_user.organization_id)
        assert "194 significant genes" in data.comparison_targets[0]["claim_text"]


class TestTheBasisIsFilledWithoutAskingAModel:
    @pytest.mark.asyncio
    async def test_a_unit_that_states_the_basis_fills_the_column(self, session, admin_user):
        """The parser already reads it correctly; asking a model for a derivable value adds cost and
        a failure mode for nothing."""
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        plan = await ReproductionPlanService.create_plan(
            session, study, admin_user.id, accessions=["GSE1"], pipeline_key="nf-core/rnaseq"
        )
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [{"metric_key": "sequencing_depth", "claimed_value": 44_600_000, "unit": "reads per library (mean)"}],
        )
        assert created[0].measurement_basis == "library"

    @pytest.mark.asyncio
    async def test_an_explicit_basis_is_not_overwritten(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        plan = await ReproductionPlanService.create_plan(
            session, study, admin_user.id, accessions=["GSE1"], pipeline_key="nf-core/rnaseq"
        )
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [{"metric_key": "depth", "claimed_value": 1, "unit": "reads per cell", "measurement_basis": "sample"}],
        )
        assert created[0].measurement_basis == "sample"

    @pytest.mark.asyncio
    async def test_a_unit_that_states_nothing_leaves_it_null(self, session, admin_user):
        """Not knowing is not disagreeing."""
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        plan = await ReproductionPlanService.create_plan(
            session, study, admin_user.id, accessions=["GSE1"], pipeline_key="nf-core/rnaseq"
        )
        created = await ReproductionPlanService.add_comparison_targets(
            session, plan, [{"metric_key": "peak_count", "claimed_value": 45000, "unit": "peaks"}]
        )
        assert created[0].measurement_basis is None


class TestTheReportSaysWhatWasNotAttempted:
    def _render(self, *, finding=None, comparisons=None, supplements=None):
        from app.services.provenance.markdown_renderer import _append_what_was_not_attempted

        parts: list[str] = []
        _append_what_was_not_attempted(
            parts,
            {"finding_claim": finding or {}},
            {"supplements": supplements or []},
            {"comparisons": comparisons or []},
        )
        return "\n".join(parts)

    def test_it_says_no_finding_was_selected(self):
        assert "No finding was selected" in self._render()

    def test_it_says_no_reproduction_was_attempted(self):
        assert "No reproduction was attempted" in self._render()

    def test_inspected_supplements_are_not_presented_as_validation(self):
        """A report that lists them without that statement invites the opposite reading."""
        rendered = self._render(
            supplements=[{"resolved": True, "role": "results_table", "label": "Supplemental File S3"}]
        )
        assert "not validation of any claim" in rendered

    def test_a_run_that_did_both_says_nothing(self):
        rendered = self._render(
            finding={"confirmed": True, "finding_set": {"genes": []}},
            comparisons=[{"metric_key": "x", "verdict": "agree"}],
        )
        assert rendered.strip() == ""
