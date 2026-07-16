"""B2/B3 reproduction-plan extractor (lit_validation, the AI comprehension core).

Covers the pure pieces (pipeline mapper, fenced-JSON parser) and the orchestration that submits full
text to the org's LLM and persists a ReproductionPlan + ComparisonTargets. The LLM client and the
provider-config lookup are faked so the orchestration is deterministic.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.comparison_target import ComparisonTarget
from app.services import validation_extraction_service as ext
from app.services.pipeline_mapper import map_method
from app.services.validation_extraction_service import (
    ValidationExtractionService,
    build_extraction_prompt,
    parse_extraction,
)
from app.services.validation_study_service import ValidationStudyService

_GOOD = """Here is the extraction:
```json
{"accessions": ["GSE52778"],
 "sample_structure": {"organism": "Homo sapiens", "sample_count": 8, "library_layout": "PAIRED"},
 "method": {"assay": "bulk RNA-seq", "tools": ["TopHat", "Cufflinks"], "reference_build": "GRCh37", "key_params": {"aligner": "tophat"}},
 "claims": [{"metric_key": "alignment_rate", "value": 83.4, "unit": "%", "tolerance": 0.05, "source_locator": "Results"},
            {"metric_key": "de_genes", "value": 316, "unit": "count", "source_locator": "Fig 3"}],
 "data_availability": "deposited", "blockers": []}
```
Done."""


def _fake_client(response):
    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    return _C()


def _patch_llm(monkeypatch, response, provider="anthropic", model="claude-opus-4-8"):
    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider=provider, model=model, api_key=None)

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _fake_client(response))


# ---- B3 pipeline mapper (pure) ----


def test_map_method_maps_bulk_rnaseq():
    m = map_method("bulk RNA-seq", tools=["TopHat", "Cufflinks"])
    assert m.pipeline_key == "nf-core/rnaseq"
    assert m.mapping_confidence in ("partial", "exact")
    assert m.blockers == []


def test_map_method_maps_single_cell():
    m = map_method("scRNA-seq", tools=["Cell Ranger"])
    assert m.pipeline_key == "nf-core/scrnaseq"


def test_map_method_maps_chipseq():
    # lit_validation Phase 4: a ChIP-seq paper now reaches the run instead of early-exiting
    # not_reproducible. Default confidence is partial (pipeline substitution), like RNA.
    m = map_method("ChIP-seq", tools=["Bowtie2", "MACS2"])
    assert m.pipeline_key == "nf-core/chipseq"
    assert m.mapping_confidence == "partial"
    assert m.blockers == []


def test_map_method_chipseq_exact_when_paper_used_nf_core():
    m = map_method("ChIP-seq", tools=["nf-core/chipseq"])
    assert m.pipeline_key == "nf-core/chipseq"
    assert m.mapping_confidence == "exact"


def test_map_method_maps_chipseq_from_histone_mark_language():
    m = map_method("H3K27ac chromatin immunoprecipitation", tools=[])
    assert m.pipeline_key == "nf-core/chipseq"


def test_map_method_unmappable_yields_blocker_and_none():
    # Kept deliberately vague ("bespoke ChIP variant" contains no specific ChIP marker) so it
    # stays not_reproducible even after ChIP-seq coverage landed (Phase 4).
    m = map_method("some bespoke ChIP variant", tools=[])
    assert m.pipeline_key is None
    assert m.mapping_confidence == "none"
    assert m.blockers
    # A known-but-unsupported assay is not_reproducible, NOT the thin-methods signal.
    assert any("no nf-core equivalent" in b.lower() for b in m.blockers)
    assert not any("insufficient method detail" in b.lower() for b in m.blockers)


def test_map_method_empty_assay_signals_thin_methods():
    for assay in ("", None, "   "):
        m = map_method(assay, tools=[])
        assert m.pipeline_key is None
        assert any("insufficient method detail" in b.lower() for b in m.blockers)


# ---- B2 parser (pure) ----


def test_parse_extraction_reads_fenced_json():
    p = parse_extraction(_GOOD)
    assert p["parse_failure"] is False
    assert p["accessions"] == ["GSE52778"]
    assert p["method"]["assay"] == "bulk RNA-seq"
    assert p["claims"][0]["metric_key"] == "alignment_rate"
    assert p["data_availability"] == "deposited"


def test_parse_extraction_handles_non_json():
    p = parse_extraction("the model refused and wrote prose")
    assert p["parse_failure"] is True
    assert p["accessions"] == []
    assert p["claims"] == []


def test_build_extraction_prompt_includes_text_and_schema():
    system, payload = build_extraction_prompt("MY PAPER BODY")
    assert "MY PAPER BODY" in payload
    assert "json" in system.lower()
    assert "accessions" in system.lower()


# ---- extractor orchestration (DB + fake LLM) ----


@pytest.mark.asyncio
async def test_extract_produces_plan_targets_and_mapping(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert plan.accessions_json == ["GSE52778"]
    assert plan.pipeline_key == "nf-core/rnaseq"
    assert plan.extractor_provider == "anthropic"
    assert plan.extractor_model == "claude-opus-4-8"
    assert plan.reference_genome == "GRCh37"
    # The model's key_params ({"aligner": "tophat"}) are experimental metadata, not nf-core params,
    # so they must NOT be forwarded as pipeline parameters (that failed launch_run's param validation).
    assert plan.parameters_json == {}
    assert study.reproduction_plan_id == plan.id

    targets = list(
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))).scalars()
    )
    assert {t.metric_key for t in targets} == {"alignment_rate", "de_genes"}
    de = next(t for t in targets if t.metric_key == "de_genes")
    assert de.claimed_value == 316.0  # ints coerced to float for the numeric column


def test_normalize_reference_genome_maps_aliases_and_drops_unknowns():
    assert ext._normalize_reference_genome("GRCh38 / Gencode 29") == "GRCh38"
    assert ext._normalize_reference_genome("hg19") == "GRCh37"
    assert ext._normalize_reference_genome("mm10") == "GRCm38"
    assert ext._normalize_reference_genome("T2T-CHM13v2.0") == "T2T-CHM13"
    assert ext._normalize_reference_genome("some exotic assembly") is None
    assert ext._normalize_reference_genome("") is None
    assert ext._normalize_reference_genome(None) is None


@pytest.mark.asyncio
async def test_extract_normalizes_composite_reference_genome(session, admin_user, monkeypatch):
    """The model reports a composite build like 'GRCh38 / Gencode 29'; the plan must carry the
    controlled-vocab token 'GRCh38' or launch_run 422s at setup (live smoke, 2026-07-05)."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD.replace('"reference_build": "GRCh37"', '"reference_build": "GRCh38 / Gencode 29"'))

    plan = await ValidationExtractionService.extract(
        session, study, "txt", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert plan.reference_genome == "GRCh38"
    assert plan.parameters_json == {}


@pytest.mark.asyncio
async def test_extract_flags_missing_data_when_no_accession(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    no_data = (
        '```json\n{"accessions": [], "method": {"assay": "bulk RNA-seq"}, "claims": [], '
        '"data_availability": "none", "blockers": []}\n```'
    )
    _patch_llm(monkeypatch, no_data)

    plan = await ValidationExtractionService.extract(
        session, study, "txt", admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert plan.accessions_json == []
    assert any("accession" in b.lower() for b in (plan.blockers_json or []))


@pytest.mark.asyncio
async def test_extract_on_parse_failure_records_blocker_not_crash(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, "the model returned prose, no JSON")

    plan = await ValidationExtractionService.extract(
        session, study, "txt", admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert plan.pipeline_key is None
    assert any("could not" in b.lower() or "parse" in b.lower() for b in (plan.blockers_json or []))


@pytest.mark.asyncio
async def test_extract_requires_active_provider(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()

    async def none_active(sess, org_id):
        return None

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", none_active)
    with pytest.raises(Exception):
        await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
