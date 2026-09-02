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
from app.services.pipeline_mapper import is_library_strategy_conflict, map_method
from app.services.validation_classifier_service import CONTROLLED_METRIC_KEYS
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


def _fake_bind(*rows):
    """Stand in for the binding call with a fixed set of decisions, one per claim index."""

    async def _bind(claims, *, client, model, api_key):
        by_index = {r["claim_index"]: r for r in rows}
        return [
            by_index.get(i, {"claim_index": i, "bound_key": None, "reason": "no decision", "confidence": 0.0})
            for i in range(len(claims))
        ]

    return _bind


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


# ---- the scoped accession's own library strategy routes the plan (plan_4 step 1) ----

_COMPOUND = """```json
{"accessions": ["GSE228658"],
 "sample_structure": {"organism": "Homo sapiens", "sample_count": 6},
 "method": {"assay": "RRBS and RNA-seq", "tools": ["Bismark"], "reference_build": "GRCh38"},
 "differential_design": {"contrasts": [], "thresholds": {}},
 "claims": [], "data_availability": "deposited", "blockers": []}
```"""

_BISULFITE_ENA = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
    "SRR1\tSRX1\tSRS1\tMeth 1\t\tBisulfite-Seq\n"
    "SRR2\tSRX2\tSRS2\tMeth 2\t\tBisulfite-Seq\n"
)


def _serve_ena(monkeypatch, accession, tsv):
    from app.services.literature import accession_manifest_service as ams

    async def _fetch(url: str) -> str:
        if url == ams._ena_filereport_url(accession):
            return tsv
        raise RuntimeError(f"unexpected fetch {url}")

    monkeypatch.setattr(ams, "_http_fetch_text", _fetch)


async def _methylseq_in_registry(session):
    from app.models.nf_core_registry_pipeline import NfCoreRegistryPipeline

    session.add(
        NfCoreRegistryPipeline(
            name="methylseq",
            full_name="nf-core/methylseq",
            description="Methylation (Bisulfite-Sequencing) analysis pipeline",
            topics=["methylation", "bisulfite-sequencing"],
            releases_json=[{"tag_name": "2.6.0"}],
            latest_release="2.6.0",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_a_scoped_bisulfite_accession_plans_methylseq_not_rnaseq(session, admin_user, monkeypatch):
    """Studies 14 and 15, in one test. Both papers say "RRBS and RNA-seq" or its equivalent, both
    were scoped to a Bisulfite-Seq accession, and both were planned against the pipeline the prose
    named first and declined."""
    await _methylseq_in_registry(session)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    assert plan.pipeline_key == "nf-core/methylseq"
    assert plan.pipeline_version == "2.6.0"
    assert "Bisulfite-Seq" in (plan.mapping_notes or "")


@pytest.mark.asyncio
async def test_an_unreachable_deposit_leaves_the_paper_to_decide(session, admin_user, monkeypatch):
    """The strategy lookup is evidence, not a gate. ENA being down must not stop a study being
    planned, and it must not change the plan it would otherwise have produced."""
    await _methylseq_in_registry(session)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)  # conftest leaves the manifest fetch offline

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    assert plan.pipeline_key == "nf-core/rnaseq"


@pytest.mark.asyncio
async def test_an_unscoped_study_never_fetches_a_manifest(session, admin_user, monkeypatch):
    """With no source_accession there is nothing to read a strategy from, so the read path must not
    spend a round trip finding that out."""
    calls: list[str] = []
    from app.services.literature import accession_manifest_service as ams

    async def _record(url: str) -> str:
        calls.append(url)
        raise RuntimeError("offline")

    monkeypatch.setattr(ams, "_http_fetch_text", _record)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    assert plan.pipeline_key == "nf-core/rnaseq"
    assert calls == []


@pytest.mark.asyncio
async def test_a_plan_the_deposit_contradicts_records_it_as_a_blocker(session, admin_user, monkeypatch):
    """methylseq is neither installed nor in the registry cache, so step 1 has nothing to offer and
    the prose route stands. The plan must say, in the scientist's own gate, that the pipeline it
    names cannot read the data this study is scoped to."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    assert plan.pipeline_key == "nf-core/rnaseq"
    conflicts = [b for b in (plan.blockers_json or []) if is_library_strategy_conflict(b)]
    assert len(conflicts) == 1, plan.blockers_json
    assert "nf-core/rnaseq" in conflicts[0]
    assert "Bisulfite-Seq" in conflicts[0]
    assert "nf-core/methylseq" in conflicts[0]


@pytest.mark.asyncio
async def test_a_plan_the_deposit_agrees_with_records_no_conflict(session, admin_user, monkeypatch):
    await _methylseq_in_registry(session)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    assert plan.pipeline_key == "nf-core/methylseq"
    assert not [b for b in (plan.blockers_json or []) if is_library_strategy_conflict(b)]


@pytest.mark.asyncio
async def test_a_contradicted_plan_still_reaches_the_gate_rather_than_early_exiting(session, admin_user, monkeypatch):
    """The blocker is a refusal to RUN, not a verdict on the paper. `not_reproducible` would be a
    terminal claim about the science, when what actually happened is that this instance cannot run
    the pipeline the data needs."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )

    from app.services.validation_driver_service import _early_exit_classification

    assert plan.pipeline_key is not None
    assert _early_exit_classification(plan) is None


# ---- the deposit's strategy is recorded, not just acted on (2026-08-30) ----
#
# It decides which pipeline a study runs, and until now the only trace it left was a sentence in
# mapping_notes, and only when it OVERRODE the paper. A strategy that agreed with the prose left no
# record at all, so nothing could answer "was the deposit even read for this study".


async def _audit_details(session, plan_id):
    from app.models.audit_log import AuditLog

    row = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "reproduction_plan", AuditLog.entity_id == plan_id)
                .order_by(AuditLog.id.desc())
            )
        )
        .scalars()
        .first()
    )
    return (row.details_json if row else None) or {}


@pytest.mark.asyncio
async def test_the_audit_trail_records_the_strategy_that_chose_the_pipeline(session, admin_user, monkeypatch):
    await _methylseq_in_registry(session)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    details = await _audit_details(session, plan.id)
    assert details["library_strategy"] == "Bisulfite-Seq"
    assert details["pipeline_key"] == "nf-core/methylseq"


@pytest.mark.asyncio
async def test_the_audit_trail_records_a_strategy_that_merely_agreed(session, admin_user, monkeypatch):
    """The case with no other trace. The deposit said RNA-Seq, the paper said RNA-seq, nothing was
    overridden, and the record must still show the deposit was read."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0002"
    )
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)
    _serve_ena(
        monkeypatch,
        "SRP0002",
        "run_accession\texperiment_accession\tsample_accession\tsample_title\texperiment_title\tlibrary_strategy\n"
        "SRR1\tSRX1\tSRS1\tA\t\tRNA-Seq\n",
    )

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert plan.pipeline_key == "nf-core/rnaseq"
    assert (await _audit_details(session, plan.id))["library_strategy"] == "RNA-Seq"


@pytest.mark.asyncio
async def test_an_unreadable_deposit_is_recorded_as_null_not_omitted(session, admin_user, monkeypatch):
    """Absent and unread are different states and the record has to tell them apart."""
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0003"
    )
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)  # conftest leaves the manifest fetch offline

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    details = await _audit_details(session, plan.id)
    assert "library_strategy" in details
    assert details["library_strategy"] is None


@pytest.mark.asyncio
async def test_the_run_log_names_the_accession_and_what_it_declares(session, admin_user, monkeypatch, caplog):
    await _methylseq_in_registry(session)
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0001"
    )
    await session.flush()
    _patch_llm(monkeypatch, _COMPOUND)
    _serve_ena(monkeypatch, "SRP0001", _BISULFITE_ENA)

    with caplog.at_level("INFO", logger="bioaf.validation_extraction"):
        await ValidationExtractionService.extract(
            session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
        )

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SRP0001" in logged
    assert "Bisulfite-Seq" in logged


@pytest.mark.asyncio
async def test_the_run_log_says_why_a_deposit_yielded_nothing(session, admin_user, monkeypatch, caplog):
    study = await ValidationStudyService.create_study(
        session, admin_user.organization_id, admin_user.id, source_accession="SRP0003"
    )
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)

    with caplog.at_level("INFO", logger="bioaf.validation_extraction"):
        await ValidationExtractionService.extract(
            session, study, "FULL TEXT ...", admin_user.organization_id, admin_user.id
        )

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SRP0003" in logged


def test_extraction_prompt_carries_the_metric_specs_not_only_the_key_names():
    """A bare key list tells the model nothing. `peak_count` and `percent_gc` are indistinguishable
    as tokens, so the model cannot tell which key its claim means or which one can earn a verdict.

    Study 20 (10.1126/sciadv.abf2229) is the evidence: the paper's own results text says "8733
    significant peaks", `significant_peaks` is a literal alias of `peak_count`, and the extractor
    still emitted `samd1_chip_peaks`, which resolves to None and scores nothing. The spec block is
    what the model was missing: the meaning, the scale, the tier and the aliases.
    """
    system, _ = build_extraction_prompt("body")

    assert "peak_count" in system
    # The aliases are the paper's own wording, and they are the bridge from prose to key.
    assert "significant_peaks" in system
    # Scale, so a fraction claim is not reported on a percent key.
    assert "0-1" in system and "0-100" in system

    peak_line = next(line for line in system.splitlines() if line.strip().startswith("peak_count"))
    assert "count" in peak_line
    assert "significant_peaks" in peak_line
    assert peak_line.strip().split("|")[3].strip(), "peak_count reaches the model with no meaning"


def test_extraction_prompt_marks_which_keys_can_earn_a_verdict():
    """`peak_count` is the only finding-tier key in the vocabulary and the only one that can earn
    `validated` at Level 2. The model should know that binding it matters and that `percent_gc`
    does not carry the same weight."""
    system, _ = build_extraction_prompt("body")
    lines = system.splitlines()

    peak_line = next(line for line in lines if line.strip().startswith("peak_count"))
    gc_line = next(line for line in lines if line.strip().startswith("percent_gc"))

    assert "finding" in peak_line
    assert "finding" not in gc_line


def test_extraction_prompt_lists_every_controlled_key():
    """The spec block replaces the bare key list, so nothing may drop out of the model's view."""
    system, _ = build_extraction_prompt("body")
    missing = [k for k in CONTROLLED_METRIC_KEYS if k not in system]
    assert missing == []


def test_the_spec_block_states_what_bioaf_actually_computes():
    """Binding on the key alone is not enough: a paper's consensus peak count and bioAF's per-sample
    MACS2 count are both `peak_count` and are not the same number. The model can only decline if the
    prompt says which one the computed side is."""
    system, _ = build_extraction_prompt("body")
    peak_line = next(line for line in system.splitlines() if line.strip().startswith("peak_count"))
    reads_line = next(line for line in system.splitlines() if line.strip().startswith("total_sequences"))

    assert "per-sample" in peak_line
    assert "pre-trim" in reads_line or "before trimming" in reads_line.lower()
    # And the model is told what to do about it, not merely informed.
    assert "basis" in system.lower()


# ---- plan_6 step 2: the binding call (pure prompt/parse) ----


_CLAIMS = [
    {"metric_key": "samd1_chip_peaks", "value": 8733, "unit": "peaks", "source_locator": "Fig. 1G"},
    {"metric_key": "peaks_gained_accessibility", "value": 1089, "unit": "peaks", "source_locator": "Fig. 2A"},
]


def _binding_response(*rows) -> str:
    import json as _json

    return "Here:\n```json\n" + _json.dumps({"bindings": list(rows)}) + "\n```\n"


def test_the_binding_prompt_shows_the_claims_and_the_vocabulary():
    """The binding call re-reads the claims against the same specs, one focused decision at a time.
    It needs the claim's own words (key, value, unit, where in the paper) and the vocabulary."""
    system, payload = ext.build_binding_prompt(_CLAIMS)

    assert "samd1_chip_peaks" in payload
    assert "8733" in payload
    assert "Fig. 1G" in payload
    assert "peak_count" in system
    # Declining has to be presented as a real answer, or the model binds everything.
    assert "decline" in system.lower()
    # And the basis rule, so it does not bind a consensus count to a per-sample one.
    assert "basis" in system.lower()


def test_parse_binding_reads_the_fenced_block():
    rows = ext.parse_binding(
        _binding_response(
            {
                "claim_index": 0,
                "bound_key": "peak_count",
                "reason": "the paper's headline peak number",
                "confidence": 0.94,
            },
            {"claim_index": 1, "bound_key": None, "reason": "a per-condition subset, not a total", "confidence": 0.9},
        )
    )
    assert [r["bound_key"] for r in rows] == ["peak_count", None]
    assert rows[0]["confidence"] == 0.94
    assert rows[1]["reason"] == "a per-condition subset, not a total"


def test_parse_binding_never_raises_on_junk():
    assert ext.parse_binding("no json here") == []
    assert ext.parse_binding("```json\nnot json\n```") == []


def test_parse_binding_refuses_a_key_outside_the_vocabulary():
    """`Never invent a key outside the vocabulary` has to be enforced, not merely requested: an
    invented key would be persisted as bound and compared against nothing."""
    rows = ext.parse_binding(
        _binding_response({"claim_index": 0, "bound_key": "samd1_peaks_v2", "reason": "looks right", "confidence": 0.9})
    )
    assert rows[0]["bound_key"] is None
    assert "samd1_peaks_v2" in rows[0]["reason"]
    assert rows[0]["confidence"] == 0.0


def test_parse_binding_clamps_confidence():
    rows = ext.parse_binding(
        _binding_response(
            {"claim_index": 0, "bound_key": "peak_count", "reason": "r", "confidence": 4},
            {"claim_index": 1, "bound_key": "frip", "reason": "r", "confidence": "nonsense"},
        )
    )
    assert rows[0]["confidence"] == 1.0
    assert rows[1]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_bind_claims_binds_the_headline_metric_and_declines_the_subset():
    """Study 20's claim binds; study 5's per-condition subset declines with a reason. Both answers
    are decisions the model made and both are recorded."""
    client = _fake_client(
        _binding_response(
            {
                "claim_index": 0,
                "bound_key": "peak_count",
                "reason": "8733 significant peaks is the paper's total",
                "confidence": 0.94,
            },
            {
                "claim_index": 1,
                "bound_key": None,
                "reason": "peaks gained in one condition is a subset",
                "confidence": 0.88,
            },
        )
    )
    rows = await ext.bind_claims(_CLAIMS, client=client, model="claude-opus-4-8", api_key=None)

    assert rows[0]["bound_key"] == "peak_count"
    assert rows[0]["reason"]
    assert rows[1]["bound_key"] is None
    assert rows[1]["reason"]


@pytest.mark.asyncio
async def test_bind_claims_returns_a_row_per_claim_even_when_the_model_skips_one():
    """A short or scrambled answer must not silently drop a claim: every claim gets a row, and a
    claim the model said nothing about is an undecided one rather than a missing one."""
    client = _fake_client(
        _binding_response({"claim_index": 1, "bound_key": "peak_count", "reason": "r", "confidence": 0.5})
    )
    rows = await ext.bind_claims(_CLAIMS, client=client, model="m", api_key=None)

    assert len(rows) == 2
    assert rows[0]["bound_key"] is None
    assert rows[1]["bound_key"] == "peak_count"


@pytest.mark.asyncio
async def test_bind_claims_on_no_claims_makes_no_call():
    class _Boom:
        async def submit(self, **kwargs):
            raise AssertionError("must not call the model with nothing to bind")

    assert await ext.bind_claims([], client=_Boom(), model="m", api_key=None) == []


# ---- plan_6 step 3: the binding decision is recorded on the target ----


@pytest.mark.asyncio
async def test_a_target_records_who_bound_it_and_why(session, admin_user, monkeypatch):
    """An AI decision that cannot be attributed is a defect. The target carries what was chosen, why,
    how sure the model was, and which model made the call."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)
    monkeypatch.setattr(
        ext,
        "bind_claims",
        _fake_bind(
            {
                "claim_index": 0,
                "bound_key": "reads_mapped_genome",
                "reason": "the paper's alignment rate",
                "confidence": 0.92,
            },
            {
                "claim_index": 1,
                "bound_key": None,
                "reason": "a DE gene count has no controlled metric",
                "confidence": 0.9,
            },
        ),
    )

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    by_key = {t.metric_key: t for t in targets}

    bound = by_key["alignment_rate"]
    assert bound.bound_key == "reads_mapped_genome"
    assert bound.bound_by == "model"
    assert bound.bound_by_model == "claude-opus-4-8"
    assert bound.binding_reason == "the paper's alignment rate"
    assert bound.binding_confidence == 0.92

    declined = by_key["de_genes"]
    assert declined.bound_key is None
    assert declined.bound_by == "model"
    assert declined.binding_reason == "a DE gene count has no controlled metric"


@pytest.mark.asyncio
async def test_a_binding_call_that_fails_leaves_the_alias_table_in_charge(session, admin_user, monkeypatch):
    """The binding call is an improvement on the alias table, not a dependency of the extraction. A
    provider error there must not lose the plan: the claims are still the paper's claims."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(ext, "bind_claims", _boom)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    assert {t.metric_key for t in targets} == {"alignment_rate", "de_genes"}
    assert all(t.bound_key is None for t in targets)
    assert all(t.bound_by == "alias_table" for t in targets)


# ---- plan_6 step 4: a binding failure is not a silent paper ----


def _decision(index, key=None, reason="r", confidence=0.9, declined=None):
    return {
        "claim_index": index,
        "bound_key": key,
        "reason": reason,
        "confidence": confidence,
        "declined": (key is None) if declined is None else declined,
    }


class TestBindingFailureIsNamed:
    """Study 4 scored 0 of 7 and its stored reasoning read "none of the paper's claimed metrics could
    be compared to a computed QC metric", which is indistinguishable from what a genuinely
    unreproducible paper produces. A weak model must not look like a weak literature.

    Two different things produce zero bindings and they need different sentences: the model reviewed
    every claim and correctly found nothing bioAF computes, or the model failed to decide at all.
    """

    def test_no_blocker_while_anything_bound(self):
        assert ext.binding_failure_blocker([_decision(0, "peak_count"), _decision(1)]) is None

    def test_no_blocker_when_there_were_no_claims(self):
        assert ext.binding_failure_blocker([]) is None

    def test_a_paper_whose_claims_are_all_outside_the_vocabulary(self):
        """Study 4's real shape: DEG counts and gene overlaps, every one correctly declined. The
        honest sentence is about the paper's claims, not about the model."""
        blocker = ext.binding_failure_blocker(
            [
                _decision(0, None, "a DE gene count is not a controlled metric"),
                _decision(1, None, "a gene-overlap count is not a controlled metric"),
            ]
        )
        assert blocker is not None
        assert "do not correspond to any metric bioAF computes" in blocker
        assert "could not map" not in blocker

    def test_a_model_that_failed_to_decide(self):
        """The model answered nothing usable. That is a failure of the model, and the sentence has to
        say so rather than blame the paper."""
        blocker = ext.binding_failure_blocker(
            [
                _decision(0, None, "the model returned no binding decision for this claim", 0.0, declined=False),
                _decision(1, None, "the model returned no binding decision for this claim", 0.0, declined=False),
            ]
        )
        assert blocker == "The model could not map any of this paper's claims to a measurable metric."

    def test_one_undecided_claim_makes_it_a_model_failure(self):
        """A mix is not a clean decline: if any claim went unanswered the model did not review the
        paper, so the paper must not be blamed for the gap."""
        blocker = ext.binding_failure_blocker(
            [
                _decision(0, None, "a DE gene count is not a controlled metric"),
                _decision(1, None, "no decision", 0.0, declined=False),
            ]
        )
        assert "could not map" in blocker


def _counting_bind(*passes):
    """Drive bind_claims with a scripted answer per attempt, and count the attempts."""
    calls = []

    async def _bind(claims, *, client, model, api_key, previous=None):
        calls.append(previous)
        rows = passes[min(len(calls) - 1, len(passes) - 1)]
        by_index = {r["claim_index"]: r for r in rows}
        return [
            by_index.get(
                i, {"claim_index": i, "bound_key": None, "reason": "no decision", "confidence": 0.0, "declined": False}
            )
            for i in range(len(claims))
        ]

    _bind.calls = calls
    return _bind


@pytest.mark.asyncio
async def test_a_plan_that_bound_nothing_is_rebound_exactly_once(session, admin_user, monkeypatch):
    """One retry, no loop. The second attempt is given its own previous answers to reconsider."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)
    binder = _counting_bind([_decision(0, None, "not a metric"), _decision(1, None, "not a metric")])
    monkeypatch.setattr(ext, "bind_claims", binder)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert len(binder.calls) == 2
    assert binder.calls[0] is None
    assert binder.calls[1] is not None, "the second attempt must see the first attempt's answers"
    assert any("do not correspond to any metric bioAF computes" in b for b in plan.blockers_json)


@pytest.mark.asyncio
async def test_a_plan_that_bound_something_is_not_rebound(session, admin_user, monkeypatch):
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)
    binder = _counting_bind([_decision(0, "reads_mapped_genome"), _decision(1, None, "not a metric")])
    monkeypatch.setattr(ext, "bind_claims", binder)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert len(binder.calls) == 1
    assert not any("could not map" in b or "do not correspond" in b for b in plan.blockers_json)


@pytest.mark.asyncio
async def test_a_second_pass_that_binds_is_the_one_recorded(session, admin_user, monkeypatch):
    """The retry exists to be believed when it succeeds: its answer is what the plan carries, and no
    binding-failure blocker is recorded."""
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await session.flush()
    _patch_llm(monkeypatch, _GOOD)
    binder = _counting_bind(
        [_decision(0, None, "read it too narrowly"), _decision(1, None, "not a metric")],
        [_decision(0, "reads_mapped_genome", "it is the alignment rate after all"), _decision(1, None, "not a metric")],
    )
    monkeypatch.setattr(ext, "bind_claims", binder)

    plan = await ValidationExtractionService.extract(
        session, study, "FULL TEXT", admin_user.organization_id, admin_user.id
    )
    await session.commit()

    assert len(binder.calls) == 2
    targets = (
        (await session.execute(select(ComparisonTarget).where(ComparisonTarget.reproduction_plan_id == plan.id)))
        .scalars()
        .all()
    )
    bound = {t.metric_key: t.bound_key for t in targets}
    assert bound["alignment_rate"] == "reads_mapped_genome"
    assert not any("could not map" in b or "do not correspond" in b for b in plan.blockers_json)
