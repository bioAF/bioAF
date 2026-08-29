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
 "differential_design": {
   "contrasts": [{"name": "dex vs untreated", "test_condition": "dexamethasone", "reference_condition": "untreated",
                  "test_samples": ["GSM1", "GSM2"], "reference_samples": ["GSM3", "GSM4"]}],
   "thresholds": {"log2fc": 1.0, "padj": 0.05}},
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


def test_map_method_maps_atacseq():
    # lit_validation Phase 4: ATAC-seq maps to nf-core/atacseq at partial (like the others).
    m = map_method("ATAC-seq", tools=["Bowtie2", "MACS2"])
    assert m.pipeline_key == "nf-core/atacseq"
    assert m.mapping_confidence == "partial"
    assert m.blockers == []


def test_map_method_atacseq_from_transposase_language():
    m = map_method("assay for transposase-accessible chromatin with sequencing", tools=[])
    assert m.pipeline_key == "nf-core/atacseq"


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


def test_parse_extraction_reads_differential_design():
    # B2e (ADR-069): the extractor stops dropping the differential design. The contrast(s),
    # the condition->sample mapping, and the paper's significance thresholds are captured as a
    # structured object so the C1 gate can ratify them and Level-3 can run them.
    p = parse_extraction(_GOOD)
    design = p["differential_design"]
    assert design["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    assert len(design["contrasts"]) == 1
    c = design["contrasts"][0]
    assert c["test_condition"] == "dexamethasone"
    assert c["reference_condition"] == "untreated"
    assert c["test_samples"] == ["GSM1", "GSM2"]
    assert c["reference_samples"] == ["GSM3", "GSM4"]


def test_parse_extraction_differential_design_absent_is_empty():
    # A QC-only paper (no differential finding) yields an empty design, never a fabricated one.
    p = parse_extraction(
        '```json\n{"accessions": [], "method": {"assay": "bulk RNA-seq"}, "claims": [], '
        '"data_availability": "none", "blockers": []}\n```'
    )
    assert p["differential_design"] == {"contrasts": [], "thresholds": {"log2fc": None, "padj": None}}


def test_parse_extraction_differential_design_tolerates_partial():
    # honest-None on missing sub-fields; a bare contrast with no sample lists still parses.
    p = parse_extraction(
        '```json\n{"differential_design": {"contrasts": [{"name": "KO vs WT"}], "thresholds": {"padj": 0.01}}}\n```'
    )
    design = p["differential_design"]
    assert design["contrasts"] == [
        {
            "name": "KO vs WT",
            "test_condition": None,
            "reference_condition": None,
            "test_samples": [],
            "reference_samples": [],
            "subjects": {},  # canonical shape now carries the optional matched-pairs map (empty = unpaired)
        }
    ]
    assert design["thresholds"] == {"log2fc": None, "padj": 0.01}


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
    # B2e: the differential design is captured on the plan (dropped before), so Level-3 can run it.
    design = plan.differential_design_json
    assert design is not None
    assert design["thresholds"] == {"log2fc": 1.0, "padj": 0.05}
    assert design["contrasts"][0]["test_samples"] == ["GSM1", "GSM2"]
    assert study.reproduction_plan_id == plan.id

    targets = list(
        (
            await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id))
        ).scalars()
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

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
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

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()
    assert plan.accessions_json == []
    assert any("accession" in b.lower() for b in (plan.blockers_json or []))


@pytest.mark.asyncio
async def test_extract_on_parse_failure_records_blocker_not_crash(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, "the model returned prose, no JSON")

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
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


@pytest.mark.asyncio
async def test_extract_persists_the_papers_tool_list(session, admin_user, monkeypatch):
    """The extractor already reads which tools the paper used, and bioAF used to spend that on one
    boolean (`_mentions_nf_core`) and a prose sentence, then throw the structured list away. It is the
    only input an honest divergence attribution has: knowing the paper called cells with CellRanger
    while we called them with STARsolo is what turns an unexplained cell-count divergence into an
    expected difference between two tools."""
    _patch_llm(monkeypatch, _GOOD)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    plan = await ValidationExtractionService.extract(
        session, study, "full text", admin_user.organization_id, admin_user.id
    )
    assert plan.tools_json == ["TopHat", "Cufflinks"]


@pytest.mark.asyncio
async def test_extract_persists_an_empty_tool_list_when_the_paper_states_none(session, admin_user, monkeypatch):
    """An empty list, never null: attribution reads this on every divergence and a None would make
    every caller defensive about a value that is simply 'the paper named no tools'."""
    no_tools = _GOOD.replace('"tools": ["TopHat", "Cufflinks"], ', "")
    _patch_llm(monkeypatch, no_tools)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    plan = await ValidationExtractionService.extract(
        session, study, "full text", admin_user.organization_id, admin_user.id
    )
    assert plan.tools_json == []


# ---- B3 fallback: a paper outside the declared routes (plan_1) ----

_PROTEOMICS = _GOOD.replace(
    '"assay": "bulk RNA-seq", "tools": ["TopHat", "Cufflinks"]',
    '"assay": "label-free quantitative proteomics", "tools": ["MaxQuant"]',
)


@pytest.mark.asyncio
async def test_extract_reaches_a_pipeline_no_declared_route_covers(session, admin_user, monkeypatch):
    """The whole point of the fallback, exercised through the real extraction path: a proteomics
    paper used to end at not_reproducible before any compute, on an instance that had quantms
    installed and used it every week."""
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    session.add(
        NfCoreRegistryPipeline(
            name="quantms",
            full_name="nf-core/quantms",
            description="Quantitative mass spectrometry workflow",
            topics=["proteomics", "mass-spectrometry", "dia", "dda", "openms"],
            releases_json=[{"tag_name": "1.3.0"}],
            latest_release="1.3.0",
        )
    )
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _PROTEOMICS)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert plan.pipeline_key == "nf-core/quantms"
    assert plan.pipeline_version == "1.3.0"
    assert plan.mapping_confidence == "partial"
    # The plan must say the reach is capped, since nobody has read what this pipeline emits.
    assert "QC-level evidence" in (plan.mapping_notes or "")
    assert not any("no nf-core equivalent" in (b or "").lower() for b in (plan.blockers_json or []))


@pytest.mark.asyncio
async def test_extract_still_prefers_the_declared_route(session, admin_user, monkeypatch):
    """Regression: a registry row that scores against RNA words must never displace nf-core/rnaseq,
    which is the only one of the two with a verified Level-3 route."""
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    session.add(
        NfCoreRegistryPipeline(
            name="decoy",
            full_name="nf-core/decoy",
            description="bulk RNA-seq transcriptome quantification",
            topics=["rna-seq", "transcriptomics"],
            releases_json=[{"tag_name": "9.9.9"}],
            latest_release="9.9.9",
        )
    )
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert plan.pipeline_key == "nf-core/rnaseq"
    assert plan.pipeline_version == "3.14.0"


@pytest.mark.asyncio
async def test_extract_keeps_the_papers_own_words_for_its_reference(session, admin_user, monkeypatch):
    """`reference_genome` is a controlled token, so 'GRCh38 / Gencode 29' and 'GRCh38 / Ensembl 112'
    both collapse to 'GRCh38' -- and the ANNOTATION, which is the half that decides which genes exist
    and what they are called, is lost.

    That matters twice over. It is a real, attributable source of divergence in a DEG concordance
    (GENCODE and Ensembl do not carry the same gene set), and it is what tells the launch path
    whether bioAF is supplying its own reference at all. The column already exists; the extractor
    read the raw string and then dropped it on the floor."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD.replace('"reference_build": "GRCh37"', '"reference_build": "GRCh38 / Gencode 29"'))

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()

    assert plan.reference_genome == "GRCh38"
    assert plan.reference_build == "GRCh38 / Gencode 29"


@pytest.mark.asyncio
async def test_extract_leaves_the_raw_build_null_when_the_paper_states_none(session, admin_user, monkeypatch):
    """A paper that never names a reference must not gain one by inference."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD.replace('"reference_build": "GRCh37"', '"reference_build": ""'))

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()

    assert plan.reference_build is None


def test_the_extraction_prompt_asks_for_the_annotation_not_only_the_assembly():
    """The assembly alone is not the reference. A paper aligned to GRCh38 with GENCODE v32 and one
    aligned to GRCh38 with Ensembl 112 do not share a gene set, and the difference lands in the DEG
    list we are scored against, so the prompt has to ask for both."""
    system, _ = ext.build_extraction_prompt("body")
    assert "annotation" in system.lower()


@pytest.mark.asyncio
async def test_extract_scopes_accessions_to_the_requested_one(session, admin_user, monkeypatch):
    """A requester who names the study's accession has scoped it, and the plan must obey.

    Real case: 10.1038/s41598-021-93509-w deposits GSE157174 and also CITES GSE114064
    (transcriptomic) and GSE118189 (a different lab's ATAC). The extractor returned all three, and
    since nothing edits `accessions_json` afterwards, approving would have fetched two irrelevant
    series, one of which is not even the right assay.

    `source_accession` was already on the request and was decorative: stored, displayed, and never
    consulted. It is the human's own statement of which dataset this study reproduces.
    """
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE157174"
    )
    await session.flush()
    three = _GOOD.replace('"accessions": ["GSE52778"]', '"accessions": ["GSE157174", "GSE114064", "GSE118189"]')
    _patch_llm(monkeypatch, three)

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()

    assert plan.accessions_json == ["GSE157174"]
    # The dropped ones are named at the C1 gate rather than discarded silently, so the human can see
    # the extractor disagreed and say so.
    dropped = [b for b in (plan.blockers_json or []) if "GSE114064" in b and "GSE118189" in b]
    assert dropped, plan.blockers_json


@pytest.mark.asyncio
async def test_extract_keeps_every_accession_when_none_was_requested(session, admin_user, monkeypatch):
    """With no `source_accession`, nothing has been scoped, so the extractor's list stands."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    three = _GOOD.replace('"accessions": ["GSE52778"]', '"accessions": ["GSE157174", "GSE114064"]')
    _patch_llm(monkeypatch, three)

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()

    assert plan.accessions_json == ["GSE157174", "GSE114064"]
    assert not [b for b in (plan.blockers_json or []) if "not the accession this study was requested for" in b]


@pytest.mark.asyncio
async def test_extract_honours_a_requested_accession_the_extractor_missed(session, admin_user, monkeypatch):
    """The requester's accession wins even when the model did not find it in the text.

    The paper is often not open access (study 11's body had to be assembled by hand), so the
    requester can easily know the accession when the extracted text does not state it.
    """
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="GSE157174"
    )
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)  # model returns only GSE52778

    plan = await ValidationExtractionService.extract(session, study, "txt", admin_user.organization_id, admin_user.id)
    await session.commit()

    assert plan.accessions_json == ["GSE157174"]
