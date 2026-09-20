"""plan_8_5 gate 1: one documentary obligation, end to end, with its exact point consequence.

The obligation is S1.B, the deposit's own sample records against the organism the paper states. It
is proved here the way the gate asks: real metadata retrieval through the production caller, the
outcome persisted, the score published, and the same numbers on the report, the list and the export,
with no analysis input acquired and no compute approved.

Three cases, and the point movement between them is the evidence that the check is real: records
that agree, records that contradict, and records bioAF could not retrieve at all.
"""

import pytest

from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_assessment import run_assessment
from app.services.validation_report_summary import (
    compact_scorecards_for,
    report_summary_for,
)
from app.services.validation_study_service import ValidationStudyService

_EXPERIMENTS = [
    {
        "id": "e1",
        "assay": "bulk RNA-seq",
        "organism": "Homo sapiens",
        "workflow": "nf-core/rnaseq",
        "reference": {
            "assembly": {"stated": "hg38", "resolved": "GRCh38", "status": "usable"},
            "annotation": {"stated": "GENCODE v44", "resolved": "GENCODE v44", "status": "usable"},
        },
    }
]


def _matrix(organism: str) -> str:
    return "\n".join(
        [
            '!Sample_title\t"WT rep1"\t"KO rep1"',
            '!Sample_geo_accession\t"GSM1"\t"GSM2"',
            f'!Sample_organism_ch1\t"{organism}"\t"{organism}"',
            '!Sample_source_name_ch1\t"HepG2 cells"\t"HepG2 cells"',
            "",
        ]
    )


def _serving(body: str | None):
    async def fetch(url: str) -> str:
        if body is None:
            raise RuntimeError("the series matrix is unreachable")
        return body

    return fetch


async def _assessed(session, admin_user, *, matrix: str | None, archive: str = "geo"):
    """A study whose deposit was opened (or could not be), assessed through the production stage."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_doi="10.1/gate1", intended_route="deposit"
    )
    plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=[])
    plan.reported_experiments_json = _EXPERIMENTS
    plan.sample_sheet_json = {"organism": "Homo sapiens", "sample_count": 2}
    study.state = "acquiring_processed"
    study.evidence_json = {"capabilities": {"deposits": [{"accession": "GSE9", "archive": archive}]}}
    await session.flush()
    await run_assessment(session, study, fetcher=_serving(matrix))
    await session.flush()
    return study


def _obligation(card: dict, leaf: str) -> dict:
    return next(
        row
        for section in card["sections"]
        for criterion in section["criteria"]
        for row in criterion["obligations"]
        if row["leaf"] == leaf
    )


class TestTheDepositsRecordsSettleTheSpeciesObligation:
    @pytest.mark.asyncio
    async def test_records_that_agree_earn_the_obligation_its_points(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=_matrix("Homo sapiens"))
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        row = _obligation(card, "S1.B")
        assert row["outcome"] == "verified"
        assert row["points"] == 1.5
        assert "GSM1" not in row["rationale"], "an agreement names what it read, not a list of samples"
        assert "Homo sapiens" in row["rationale"]

    @pytest.mark.asyncio
    async def test_records_that_contradict_the_paper_are_a_negative_with_their_cause(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=_matrix("Mus musculus"))
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        row = _obligation(card, "S1.B")
        assert row["outcome"] == "failed"
        assert row["impact"]
        assert "Mus musculus" in row["rationale"]

    @pytest.mark.asyncio
    async def test_correcting_only_that_defect_moves_only_that_obligation(self, session, admin_user):
        """plan_8_5 section 4's matched repaired control: the same paper, one fact changed."""
        wrong = await _assessed(session, admin_user, matrix=_matrix("Mus musculus"))
        right = await _assessed(session, admin_user, matrix=_matrix("Homo sapiens"))
        bad = (await report_summary_for(session, wrong, admin_user.organization_id))["evidence_score"]
        good = (await report_summary_for(session, right, admin_user.organization_id))["evidence_score"]
        assert good["score"] - bad["score"] == 1.5
        assert bad["failed"] - good["failed"] == 1.5
        assert bad["undetermined"] == good["undetermined"], "nothing else moved"
        assert bad["scope"]["assessed"] == good["scope"]["assessed"]

    @pytest.mark.asyncio
    async def test_a_deposit_bioaf_could_not_read_leaves_it_untested_and_deducts_nothing(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=None)
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        row = _obligation(card, "S1.B")
        assert row["outcome"] == "undetermined"
        assert card["failed"] == 0
        assert "could not be read" in row["rationale"]

    @pytest.mark.asyncio
    async def test_an_archive_bioaf_cannot_open_is_named_as_bioafs_own_limit(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=None, archive="ega")
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        row = _obligation(card, "S1.B")
        assert row["outcome"] == "undetermined"
        assert "GEO" in row["rationale"]


class TestTheWholeApplicationShowsTheSameAnswer:
    @pytest.mark.asyncio
    async def test_the_list_cell_and_the_report_agree(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=_matrix("Mus musculus"))
        report = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        listed = (await compact_scorecards_for(session, [study]))[study.id]["evidence_score"]
        assert listed["headline"] == report["headline"]
        assert listed["parts"] == report["parts"]

    @pytest.mark.asyncio
    async def test_the_score_was_published_before_any_input_was_acquired(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=_matrix("Homo sapiens"))
        evidence = study.evidence_json or {}
        assert evidence["rubric_assessment"]["outcomes"]["S1.B"]["outcome"] == "verified"
        assert evidence["scorecard_record"]["evidence_score"]["score"] > 0
        assert "deposit" not in evidence, "nothing was acquired"
        assert "input_choice" not in evidence, "no analysis input was chosen"

    @pytest.mark.asyncio
    async def test_opening_the_report_publishes_nothing_new(self, session, admin_user):
        study = await _assessed(session, admin_user, matrix=_matrix("Homo sapiens"))
        before = dict(study.evidence_json or {})
        await report_summary_for(session, study, admin_user.organization_id)
        assert (study.evidence_json or {}) == before


class TestTheSampleObligationsDoNotWaitForAnAnalysisInput:
    """plan_8_5 gate 2: S2 to S5 are documentary checks. They are settled from the deposit's own
    records, before any count matrix is selected and before any mapping is accepted."""

    _RICH = "\n".join(
        [
            '!Sample_title\t"WT rep1"\t"WT rep2"\t"KO rep1"\t"KO rep2"',
            '!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\t"GSM4"',
            '!Sample_organism_ch1\t"Homo sapiens"\t"Homo sapiens"\t"Homo sapiens"\t"Homo sapiens"',
            '!Sample_source_name_ch1\t"HepG2 cells"\t"HepG2 cells"\t"HepG2 cells"\t"HepG2 cells"',
            '!Sample_characteristics_ch1\t"condition: WT"\t"condition: WT"\t"condition: KO"\t"condition: KO"',
            '!Sample_characteristics_ch1\t"replicate: 1"\t"replicate: 2"\t"replicate: 1"\t"replicate: 2"',
            "",
        ]
    )

    async def _study(self, session, admin_user):
        study = await ValidationStudyService.create_study(
            session, admin_user.organization_id, admin_user.id, source_doi="10.1/gate2", intended_route="deposit"
        )
        plan = await ReproductionPlanService.create_plan(session, study, admin_user.id, accessions=[])
        plan.reported_experiments_json = _EXPERIMENTS
        plan.sample_sheet_json = {"organism": "Homo sapiens", "sample_count": 4}
        plan.differential_design_json = {
            "contrasts": [{"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT"}]
        }
        study.state = "acquiring_processed"
        study.evidence_json = {"capabilities": {"deposits": [{"accession": "GSE9", "archive": "geo"}]}}
        await session.flush()
        await run_assessment(session, study, fetcher=_serving(self._RICH))
        await session.flush()
        return study

    @pytest.mark.asyncio
    async def test_the_counts_reconcile_without_a_chosen_input(self, session, admin_user):
        study = await self._study(session, admin_user)
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        assert _obligation(card, "S4.B")["outcome"] == "verified"
        assert "input_choice" not in (study.evidence_json or {})

    @pytest.mark.asyncio
    async def test_the_arms_are_supported_by_the_deposits_own_records(self, session, admin_user):
        study = await self._study(session, admin_user)
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        assert _obligation(card, "S3.B")["outcome"] == "verified"

    @pytest.mark.asyncio
    async def test_the_replication_the_records_describe_settles_its_obligation(self, session, admin_user):
        study = await self._study(session, admin_user)
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        assert _obligation(card, "S5.A")["outcome"] == "verified"

    @pytest.mark.asyncio
    async def test_what_the_paper_says_about_its_material_is_still_owed(self, session, admin_user):
        """S2.A needs the paper's own statement, which the documentary judgment records. Until it
        does, the obligation is untested and says what would settle it."""
        study = await self._study(session, admin_user)
        card = (await report_summary_for(session, study, admin_user.organization_id))["evidence_score"]
        row = _obligation(card, "S2.A")
        assert row["outcome"] == "undetermined"
        assert row["next_action"]
