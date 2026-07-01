"""Tests for the recommend_pipeline shared service (ai_pipeline_run Phase 1).

recommend_pipeline is a deterministic, rule-based service: given an experiment, it
inspects the experiment's samples (molecule type, single-cell prep signals, organism)
and recommends an nf-core pipeline plus a reference genome. It is consumed both as an
agent tool (ai_pipeline_run) and, later, by lit_validation. v1 covers bulk RNA -> rnaseq
and single-cell RNA -> scrnaseq; anything else returns a "cannot recommend" result rather
than a guess.
"""

import pytest

from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.sample import Sample
from app.services.recommend_pipeline_service import RecommendPipelineService

pytestmark = pytest.mark.asyncio


# ---- Helpers ----


async def _make_experiment(session, admin_user, name="Recommend Test"):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name=name,
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


async def _add_sample(session, experiment, **fields):
    sample = Sample(experiment_id=experiment.id, **fields)
    session.add(sample)
    await session.flush()
    await session.commit()
    return sample


async def _install_pipeline(session, admin_user, pipeline_key, version, default_params):
    entry = PipelineCatalogEntry(
        organization_id=admin_user.organization_id,
        pipeline_key=pipeline_key,
        name=pipeline_key,
        source_type="github",
        source_url=f"https://github.com/{pipeline_key}",
        version=version,
        default_params_json=default_params,
        enabled=True,
    )
    session.add(entry)
    await session.flush()
    await session.commit()
    return entry


# ---- Tests ----


async def test_recommends_rnaseq_for_bulk_mouse_rna(session, admin_user):
    exp = await _make_experiment(session, admin_user)
    await _add_sample(
        session,
        exp,
        external_id="BULK_1",
        organism="Mus musculus",
        molecule_type="total RNA",
        library_prep_method="TruSeq Stranded mRNA",
    )
    await _install_pipeline(session, admin_user, "nf-core/rnaseq", "3.14.0", {"aligner": "star_salmon"})

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/rnaseq"
    assert rec.reference_genome == "GRCm39"
    assert rec.version == "3.14.0"
    assert rec.parameters == {"aligner": "star_salmon"}
    assert rec.rationale  # a non-empty plain-language explanation


async def test_recommends_scrnaseq_for_human_10x(session, admin_user):
    exp = await _make_experiment(session, admin_user, name="10x Human")
    await _add_sample(
        session,
        exp,
        external_id="SC_1",
        organism="Homo sapiens",
        molecule_type="total RNA",
        library_prep_method="10x Chromium 3' v3",
        chemistry_version="v3",
    )
    await _install_pipeline(session, admin_user, "nf-core/scrnaseq", "2.7.1", {"aligner": "cellranger"})

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/scrnaseq"
    assert rec.reference_genome == "GRCh38"
    assert rec.version == "2.7.1"
    assert rec.parameters == {"aligner": "cellranger"}


async def test_recommends_pipeline_even_when_not_installed(session, admin_user):
    """The recommendation names the right pipeline even if it is not yet in the catalog;
    version/parameters are absent and the rationale flags that it must be installed."""
    exp = await _make_experiment(session, admin_user, name="Bulk, no catalog")
    await _add_sample(
        session,
        exp,
        external_id="BULK_2",
        organism="Homo sapiens",
        molecule_type="mRNA",
        library_prep_method="TruSeq",
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/rnaseq"
    assert rec.reference_genome == "GRCh38"
    assert rec.version is None
    assert rec.parameters is None
    assert "install" in rec.rationale.lower()


async def test_cannot_recommend_for_proteomics(session, admin_user):
    exp = await _make_experiment(session, admin_user, name="Proteomics")
    await _add_sample(
        session,
        exp,
        external_id="PROT_1",
        organism="Homo sapiens",
        molecule_type="protein",
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is False
    assert rec.pipeline_key is None
    assert rec.rationale  # explains why no pipeline was recommended


async def test_cannot_recommend_when_no_samples(session, admin_user):
    exp = await _make_experiment(session, admin_user, name="Empty experiment")

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is False
    assert rec.pipeline_key is None


async def test_unmapped_organism_recommends_pipeline_without_reference(session, admin_user):
    """A mappable assay with an unmapped organism still recommends the pipeline, but the
    reference genome is left unresolved for the user to choose."""
    exp = await _make_experiment(session, admin_user, name="Zebrafish bulk")
    await _add_sample(
        session,
        exp,
        external_id="ZF_1",
        organism="Danio rerio",
        molecule_type="total RNA",
        library_prep_method="TruSeq",
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/rnaseq"
    assert rec.reference_genome is None


async def test_raises_for_unknown_experiment(session, admin_user):
    with pytest.raises(LookupError):
        await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=999999)


# ---- Hybrid assay field: explicit field beats heuristic; result carries confidence + signals ----


async def test_explicit_assay_overrides_heuristic(session, admin_user):
    """An explicit assay value wins over what the free-text heuristic would infer. The sample's
    molecule/prep fields look like bulk RNA, but assay='scrna' steers to scrnaseq with high
    confidence."""
    exp = await _make_experiment(session, admin_user, name="Explicit scRNA")
    await _add_sample(
        session,
        exp,
        external_id="EXPLICIT_SC",
        organism="Homo sapiens",
        molecule_type="total RNA",  # heuristic alone -> bulk
        library_prep_method="TruSeq",  # heuristic alone -> bulk
        assay="scrna",  # explicit field -> single cell
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/scrnaseq"
    assert rec.confidence == "high"
    assert any("assay" in s.lower() for s in rec.signals)


async def test_blank_assay_falls_back_to_heuristic(session, admin_user):
    """With no explicit assay, recommendation falls back to the free-text heuristic and reports
    medium confidence when a positive signal (single-cell prep) is present."""
    exp = await _make_experiment(session, admin_user, name="Heuristic scRNA")
    await _add_sample(
        session,
        exp,
        external_id="HEUR_SC",
        organism="Homo sapiens",
        molecule_type="total RNA",
        library_prep_method="10x Chromium 3' v3",
        chemistry_version="v3",
        assay=None,
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/scrnaseq"
    assert rec.confidence == "medium"
    assert rec.signals  # the heuristic signals it used


async def test_explicit_bulk_assay_is_high_confidence_with_signals(session, admin_user):
    exp = await _make_experiment(session, admin_user, name="Explicit bulk")
    await _add_sample(
        session,
        exp,
        external_id="EXPLICIT_BULK",
        organism="Mus musculus",
        molecule_type="total RNA",
        assay="bulk_rna",
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/rnaseq"
    assert rec.confidence == "high"
    assert rec.signals


async def test_default_only_bulk_is_low_confidence(session, admin_user):
    """When nothing but the default molecule type points to bulk RNA (no explicit assay, no prep
    method, no single-cell signal), the recommendation is made but flagged low confidence."""
    exp = await _make_experiment(session, admin_user, name="Default bulk")
    await _add_sample(
        session,
        exp,
        external_id="DEFAULT_BULK",
        organism="Mus musculus",
        molecule_type="total RNA",  # the server default; nothing else to go on
    )

    rec = await RecommendPipelineService.recommend(session, org_id=admin_user.organization_id, experiment_id=exp.id)

    assert rec.recommended is True
    assert rec.pipeline_key == "nf-core/rnaseq"
    assert rec.confidence == "low"
