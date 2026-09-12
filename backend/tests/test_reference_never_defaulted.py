"""change_7.5 section 1.3: no reference default, and a reference only where an operation needs one.

Study 38's extraction read "mm9 (ChIP-seq) / GENCODE M23 (RNA-seq transcriptome)" and wrote "could not
map the paper's reference genome ... the analysis run will use a default". The default was real: with
no genome, the launch kept the pipeline's seeded parameters, and nf-core/rnaseq is seeded with GRCh38,
so a mouse RNA-seq run would have aligned to human. bioAF knew no mm9 at all, and matched assemblies by
substring. The classifier then blamed "a reference-build mismatch" on the deposit route, where nothing
was aligned.
"""

from types import SimpleNamespace

import pytest

from app.services import validation_extraction_service as ext
from app.services.pipeline_run_service import PipelineRunService
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_classifier_service import classify_study
from app.services.validation_completion import classification_for
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_reference import (
    UNAVAILABLE,
    UNRESOLVED,
    UNSTATED,
    USABLE,
    assemblies_named,
    paper_reference,
    requires_reference,
)
from app.services.validation_report_summary import LIMITATION_LABELS
from app.services.validation_study_service import ValidationStudyService

# ---- historical assemblies are recognised as assemblies, and never substituted ----


def test_mm9_is_recognised_and_reported_unavailable_rather_than_unmapped():
    ref = paper_reference("mm9 (ChIP-seq) / GENCODE M23 (RNA-seq transcriptome)", "nf-core/chipseq")
    assert ref["status"] == UNAVAILABLE
    assert ref["stated"] == "mm9"
    assert "states mm9" in ref["reason"]
    assert "cannot supply mm9 to nf-core/chipseq" in ref["reason"]
    # Nothing substitutes the nearest current assembly.
    assert ref["assembly"] is None


@pytest.mark.parametrize(
    "text, stated",
    [
        ("NCBIM37", "mm9"),
        ("hg18", "hg18"),
        ("NCBI36", "hg18"),
        ("Rnor_6.0", "rn6"),
        ("Zv9", "danRer7"),
        ("GRCz10", "danRer10"),
        ("dm3", "dm3"),
        ("ce10", "ce10"),
        ("TAIR9", "TAIR9"),
    ],
)
def test_older_assemblies_of_organisms_bioaf_carries_are_named(text, stated):
    ref = paper_reference(text, "nf-core/rnaseq")
    assert ref["status"] == UNAVAILABLE
    assert ref["stated"] == stated


def test_a_current_assembly_bioaf_supplies_is_usable():
    ref = paper_reference("GRCm38 / Ensembl 102", "nf-core/rnaseq")
    assert ref["status"] == USABLE
    assert ref["assembly"] == "GRCm38"


def test_an_assembly_bioaf_recognises_but_cannot_supply_is_unavailable():
    ref = paper_reference("T2T-CHM13", "nf-core/rnaseq")
    assert ref["status"] == UNAVAILABLE
    assert ref["stated"] == "T2T-CHM13"


def test_text_that_names_no_assembly_is_unresolved():
    ref = paper_reference("the mouse reference genome", "nf-core/rnaseq")
    assert ref["status"] == UNRESOLVED
    assert "the mouse reference genome" in ref["reason"]


def test_a_paper_that_states_no_reference_is_unstated():
    for text in (None, "", "   "):
        ref = paper_reference(text, "nf-core/rnaseq")
        assert ref["status"] == UNSTATED
        assert ref["reason"] == "the paper does not state its reference"


def test_one_unavailable_assembly_refuses_the_whole_paper_in_stage_1():
    """One reference per paper until section 2.3: conservative, never a substitution."""
    ref = paper_reference("mm9 for ChIP-seq, GRCm38 for RNA-seq", "nf-core/rnaseq")
    assert ref["status"] == UNAVAILABLE
    assert ref["stated"] == "mm9"


# ---- matching is word-anchored ----


@pytest.mark.parametrize("text", ["mm90", "sample hg180", "ice10 buffer", "urn4", "xdm3"])
def test_an_assembly_inside_another_word_is_not_an_assembly(text):
    assert assemblies_named(text) == []


@pytest.mark.parametrize(
    "text, assembly",
    [
        ("GRCh38.p13", "GRCh38"),
        ("hg19,", "GRCh37"),
        ("refdata-gex-GRCh38-2020-A", "GRCh38"),
        ("mm10/GRCm38", "GRCm38"),
        ("GRCh38_no_alt", "GRCh38"),
        ("BDGP6.32", "BDGP6"),
        ("mRatBN7.2", "mRatBN7.2"),
    ],
)
def test_anchored_matching_still_finds_real_spellings(text, assembly):
    assert [a["assembly"] for a in assemblies_named(text)] == [assembly]


def test_the_extractor_reads_the_same_anchored_names():
    assert ext._normalize_reference_genome("mm90") is None
    assert ext._normalize_reference_genome("GRCh38.p13") == "GRCh38"


# ---- the operations that depend on a reference ----


def test_each_operation_declares_whether_it_needs_an_assembly():
    assert requires_reference("author_results") is False
    assert requires_reference("processed_reanalysis") is False
    assert requires_reference("raw_reanalysis") is True
    assert requires_reference("qc_from_run") is True
    assert requires_reference("region_assignment") is True


# ---- the extraction records the reference as it is, and never promises a default ----

_EXTRACTION = """```json
{"accessions": ["GSE1"], "method": {"assay": "bulk RNA-seq", "reference_build": "%s"},
 "differential_design": {"contrasts": []}, "claims": [], "data_availability": "deposited", "blockers": []}
```"""


def _patch_llm(monkeypatch, reference_build: str):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return _EXTRACTION % reference_build

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())


async def _extract(session, admin_user, monkeypatch, reference_build):
    _patch_llm(monkeypatch, reference_build)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    return await ext.ValidationExtractionService.extract(session, study, "x", admin_user.organization_id, admin_user.id)


@pytest.mark.asyncio
async def test_no_blocker_promises_a_default_reference(session, admin_user, monkeypatch):
    plan = await _extract(session, admin_user, monkeypatch, "the mouse genome")
    assert not any("will use a default" in b for b in plan.blockers_json)
    assert any("unresolved" in b and "the mouse genome" in b for b in plan.blockers_json)
    assert plan.reference_genome is None


@pytest.mark.asyncio
async def test_a_historical_assembly_is_named_in_the_plan(session, admin_user, monkeypatch):
    plan = await _extract(session, admin_user, monkeypatch, "mm9 (ChIP-seq) / GENCODE M23 (RNA-seq transcriptome)")
    assert plan.reference_genome is None
    assert not any("could not map" in b for b in plan.blockers_json)
    assert any("The paper states mm9" in b and "cannot supply mm9" in b for b in plan.blockers_json)


# ---- no validation launch runs on a pipeline's seeded genome ----


class _NoLaunch:
    async def __call__(self, *args, **kwargs):
        raise AssertionError("a validation launch ran without a usable reference")


async def _raw_reads_study(session, user, *, state, reference_build=None, reference_genome=None):
    from app.schemas.experiment import ExperimentCreate
    from app.services.experiment_service import ExperimentService

    study = await ValidationStudyService.create_study(session, user.organization_id, user.id, source_doi="10.1/abc")
    study.intended_route = "pipeline"
    await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["SRR390728"],
        pipeline_key="nf-core/rnaseq",
        pipeline_version="3.14.0",
        reference_build=reference_build,
        reference_genome=reference_genome,
    )
    study.state = state
    study.experiment_id = (
        await ExperimentService.create_experiment(session, user.organization_id, user.id, ExperimentCreate(name="r"))
    ).id
    study.evidence_json = {"assessment": {"at": "already run"}}
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_raw_reads_are_never_fetched_for_a_paper_whose_reference_bioaf_cannot_supply(
    session, admin_user, monkeypatch
):
    monkeypatch.setattr(PipelineRunService, "launch_run", _NoLaunch())
    study = await _raw_reads_study(session, admin_user, state="acquiring_data", reference_build="mm9")

    await ValidationDriverService._handle_acquiring_data(session, study)

    assert study.state == "classified"
    assert study.classification == "inconclusive"
    [limitation] = [
        lim for lim in study.evidence_json["completion"]["limitations"] if lim["kind"] == "reference_unavailable"
    ]
    assert "mm9" in limitation["detail"]
    assert "never substitutes" in limitation["detail"]


@pytest.mark.asyncio
async def test_the_analysis_is_never_launched_on_a_seeded_genome(session, admin_user, monkeypatch):
    monkeypatch.setattr(PipelineRunService, "launch_run", _NoLaunch())
    study = await _raw_reads_study(session, admin_user, state="setup")

    await ValidationDriverService._handle_setup(session, study)

    assert study.state == "classified"
    [limitation] = [
        lim for lim in study.evidence_json["completion"]["limitations"] if lim["kind"] == "reference_unavailable"
    ]
    assert "does not state its reference" in limitation["detail"]


@pytest.mark.asyncio
async def test_a_usable_reference_still_launches(session, admin_user, monkeypatch):
    launched = []

    async def _launch(session_, org_id, user_id, data, *, via_assistant=False):
        launched.append(data)
        raise RuntimeError("stop after the launch was attempted")

    monkeypatch.setattr(PipelineRunService, "launch_run", _launch)
    study = await _raw_reads_study(
        session, admin_user, state="acquiring_data", reference_build="GRCm38", reference_genome="GRCm38"
    )
    with pytest.raises(RuntimeError):
        await ValidationDriverService._handle_acquiring_data(session, study)
    assert launched


# ---- a reference is blamed only for a result that aligned reads ----

_DIVERGE = {
    "kind": "gene",
    "verdict": "diverge",
    "paper_n": 100,
    "our_n": 90,
    "overlap": 8,
    "concordant": 8,
    "directional_overlap_frac": 0.08,
    "enrichment_p": 0.9,
    "notes": [],
}


def test_the_deposit_route_never_blames_a_reference_it_did_not_use():
    result = classify_study(
        [],
        {},
        mapping_confidence="exact",
        reference_genome=None,
        concordance_results=[_DIVERGE],
        differential_attribution={"thresholds_matched": True, "method_comparable": True},
        route="deposit",
    )
    assert "reference-build mismatch" not in result["reasoning"]


def test_a_run_that_aligned_reads_without_a_recognised_reference_still_names_it():
    result = classify_study(
        [],
        {},
        mapping_confidence="exact",
        reference_genome=None,
        concordance_results=[_DIVERGE],
        differential_attribution={"thresholds_matched": True, "method_comparable": True},
        route=None,
    )
    assert "reference-build mismatch" in result["reasoning"]


# ---- the limitation ----


def test_reference_unavailable_is_its_own_limitation_and_never_an_absence():
    assert LIMITATION_LABELS["reference_unavailable"] == "Stated reference not available to bioAF"
    assert classification_for([{"kind": "reference_unavailable"}]) == "inconclusive"
