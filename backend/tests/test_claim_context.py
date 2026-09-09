"""change_7.1 section 3: bind a claim to the context it was actually measured in.

A comparison target carried a metric name, a value, a unit and a locator, and the comparison engine
checked the number against whatever the alias table resolved. For Groff et al. that produced three
distinct errors, each of which reads as a finding about the paper:

- 54 samples were collected; 51 were analysed after three TE biopsies were excluded. The target
  claimed 54 samples had passed final QC.
- 194 genes reached significance and 88 of those cleared |log2FC| > 2. The target combined the
  194-gene count with the 88-gene threshold.
- Six genes are UP relative to good-quality embryos. A poor-versus-good contrast reverses the sign,
  so the same six genes read as contradicting the paper.

And two of the paper's central claims (digital karyotype, TE-WE concordance) were dropped outright
because nothing in the controlled vocabulary measures them, which made the assessment look complete
when it had never read them.
"""

import pytest

from app.models.comparison_target import ComparisonTarget
from app.services.reproduction_plan_service import ReproductionPlanService


async def _plan(session, admin_user):
    from app.services.validation_study_service import ValidationStudyService

    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1101/gr.252981.119"
    )
    return await ReproductionPlanService.create_plan(session, study, admin_user.id)


class TestAClaimKeepsWhatWasMeasured:
    @pytest.mark.asyncio
    async def test_sample_subset_and_qc_stage_survive(self, session, admin_user):
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "transcripts detected",
                    "claimed_value": 10500,
                    "sample_subset": "whole embryo",
                    "qc_stage": "post-QC",
                    "output_type": "count",
                }
            ],
        )
        assert created[0].sample_subset == "whole embryo"
        assert created[0].qc_stage == "post-QC"
        assert created[0].output_type == "count"

    @pytest.mark.asyncio
    async def test_two_claims_about_one_quantity_are_distinct_targets(self, session, admin_user):
        """The paper's whole-embryo and trophectoderm transcript counts share a metric name and are
        different numbers. Without the subset they are one claim measured twice."""
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {"metric_key": "transcripts detected", "claimed_value": 10500, "sample_subset": "whole embryo"},
                {"metric_key": "transcripts detected", "claimed_value": 6200, "sample_subset": "trophectoderm"},
            ],
        )
        assert len({(t.metric_key, t.sample_subset) for t in created}) == 2

    @pytest.mark.asyncio
    async def test_a_threshold_is_stored_apart_from_the_count_it_qualifies(self, session, admin_user):
        """194 significant genes and 88 above |log2FC| > 2 are two claims. Merging them reported
        the paper as claiming 194 genes cleared a cutoff that 88 of them did."""
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {"metric_key": "significant genes", "claimed_value": 194, "threshold": 0.05, "threshold_kind": "padj"},
                {
                    "metric_key": "significant genes",
                    "claimed_value": 88,
                    "threshold": 2.0,
                    "threshold_kind": "abs_log2fc",
                },
            ],
        )
        assert {(t.claimed_value, t.threshold_kind) for t in created} == {
            (194.0, "padj"),
            (88.0, "abs_log2fc"),
        }

    @pytest.mark.asyncio
    async def test_direction_is_recorded_so_a_reversed_contrast_cannot_pass_as_agreement(self, session, admin_user):
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [{"metric_key": "upregulated genes", "claimed_value": 6, "direction": "up"}],
        )
        assert created[0].direction == "up"


class TestAClaimNothingMeasuresIsStillPreserved:
    @pytest.mark.asyncio
    async def test_a_claim_with_no_metric_is_kept_not_dropped(self, session, admin_user):
        """Groff's digital-karyotype and TE-WE concordance claims are central to the paper. They
        were dropped, so the report never said they had not been checked."""
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "",
                    "claim_text": "digital karyotype is concordant with PGT-A",
                    "source_locator": "Results",
                }
            ],
        )
        assert len(created) == 1
        assert created[0].metric_key is None
        assert created[0].claim_text == "digital karyotype is concordant with PGT-A"

    @pytest.mark.asyncio
    async def test_a_claim_with_neither_metric_nor_text_is_not_a_claim(self, session, admin_user):
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session, plan, [{"metric_key": "", "unit": "genes"}]
        )
        assert created == []

    @pytest.mark.asyncio
    async def test_the_paper_wording_is_kept_beside_a_bound_claim_too(self, session, admin_user):
        plan = await _plan(session, admin_user)
        created = await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [{"metric_key": "significant genes", "claim_text": "194 genes reached significance", "claimed_value": 194}],
        )
        assert created[0].claim_text == "194 genes reached significance"


class TestTheContextReachesThePersistedRow:
    @pytest.mark.asyncio
    async def test_every_context_field_round_trips(self, session, admin_user):
        plan = await _plan(session, admin_user)
        await ReproductionPlanService.add_comparison_targets(
            session,
            plan,
            [
                {
                    "metric_key": "significant genes",
                    "claim_text": "88 of 194 genes had |log2FC| > 2",
                    "claimed_value": 88,
                    "sample_subset": "XX vs XY",
                    "qc_stage": "post-QC",
                    "direction": "up",
                    "threshold": 2.0,
                    "threshold_kind": "abs_log2fc",
                    "output_type": "gene_set_size",
                }
            ],
        )
        await session.flush()
        row = (await session.execute(ComparisonTarget.__table__.select())).mappings().all()[0]
        assert row["sample_subset"] == "XX vs XY"
        assert row["direction"] == "up"
        assert row["threshold"] == 2.0
        assert row["output_type"] == "gene_set_size"


class TestThePreservedClaimSurvivesTheApiBoundary:
    def test_a_claim_with_no_metric_serialises(self):
        """The row is preserved in the database and then has to reach the reader. A response model
        requiring a metric name would reject exactly the claims this change exists to keep."""
        from app.schemas.validation_study import ComparisonTargetResponse

        response = ComparisonTargetResponse(
            metric_key=None,
            claim_text="digital karyotype is concordant with PGT-A",
            claimed_value=None,
        )
        assert response.metric_key is None
        assert response.claim_text == "digital karyotype is concordant with PGT-A"

    def test_the_context_reaches_the_reader(self):
        from app.schemas.validation_study import ComparisonTargetResponse

        response = ComparisonTargetResponse(
            metric_key="significant genes",
            claimed_value=88,
            sample_subset="XX vs XY",
            qc_stage="post-QC",
            direction="up",
            threshold=2.0,
            threshold_kind="abs_log2fc",
            output_type="gene_set_size",
        )
        assert response.sample_subset == "XX vs XY"
        assert response.threshold_kind == "abs_log2fc"
