"""change_7.5 sections 3.2 to 3.4 through the driver: a stage 2 plan on the deposit route chooses its
input from the selected claim and the evidence, maps its samples from the repository's records,
acquires the authors' table beside the matrix, and holds an unresolved mapping without re-retrieval;
"Review and resume" re-enters mapping with the same input.
"""

import gzip
import json
from types import SimpleNamespace

import httpx
import pytest

from app.models.organization import Organization
from app.models.validation_study import ValidationStudy
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_P = [{"kind": "pvalue", "operator": "<", "value": 0.01}]
_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE555nnn/GSE555001/suppl/"
_MATRIX = "GSE555001_normalized_counts.txt.gz"
_TABLE = "GSE555001_DESeq2_results.txt.gz"
_COLUMNS = ["WT_a", "WT_b", "KO_c5", "KO_c16", "KO_c5_d7"]
_MATRIX_TEXT = "gene\t" + "\t".join(_COLUMNS) + "\n" + "\n".join(
    f"g{i}\t" + "\t".join(f"{(i * 7 + j * 3) % 50 + 0.5:.2f}" for j in range(5)) for i in range(8)
)
_TABLE_TEXT = "gene\tlog2FoldChange\tpvalue\tpadj\ng1\t1.5\t0.001\t0.01\ng2\t-2.0\t0.004\t0.02\ng3\t0.1\t0.5\t0.9\n"
_SERIES_MATRIX = (
    "!Series_title\t\"x\"\n"
    "!Sample_title\t\"WT_a\"\t\"WT_b\"\t\"KO_c5\"\t\"KO_c16\"\t\"KO_c5_d7\"\n"
    "!Sample_geo_accession\t\"GSM1\"\t\"GSM2\"\t\"GSM3\"\t\"GSM4\"\t\"GSM5\"\n"
    "!Sample_characteristics_ch1\t\"genotype: wild type\"\t\"genotype: wild type\"\t\"genotype: SAMD1 KO\"\t"
    "\"genotype: SAMD1 KO\"\t\"genotype: SAMD1 KO\"\n"
    "!Sample_characteristics_ch1\t\"culture: WT1\"\t\"culture: WT2\"\t\"clone: c5\"\t\"clone: c16\"\t\"clone: c5\"\n"
    "!Sample_characteristics_ch1\t\"time: day 0\"\t\"time: day 0\"\t\"time: day 0\"\t\"time: day 0\"\t\"time: day 7\"\n"
)
_LISTING = f'<html><body><a href="{_MATRIX}">{_MATRIX}</a><a href="{_TABLE}">{_TABLE}</a></body></html>'


def _mapping(*, ko16_quote="clone: c16"):
    return [
        {"column": "WT_a", "arm": "reference", "biological_unit": "WT1", "time_point": "day 0",
         "evidence": [{"source": "sample_record", "quote": "culture: WT1"}]},
        {"column": "WT_b", "arm": "reference", "biological_unit": "WT2", "time_point": "day 0",
         "evidence": [{"source": "sample_record", "quote": "culture: WT2"}]},
        {"column": "KO_c5", "arm": "test", "biological_unit": "c5", "time_point": "day 0",
         "evidence": [{"source": "sample_record", "quote": "clone: c5"}]},
        {"column": "KO_c16", "arm": "test", "biological_unit": "c16", "time_point": "day 0",
         "evidence": [{"source": "sample_record", "quote": ko16_quote}]},
        {"column": "KO_c5_d7", "arm": "excluded", "evidence": [{"source": "sample_record", "quote": "time: day 7"}]},
    ]


class _Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return "```json\n" + json.dumps(answer) + "\n```"


def _patch(monkeypatch, client):
    from app.services import validation_driver_service as driver

    async def get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None)

    monkeypatch.setattr(driver.llm_provider_config_service, "get_for_feature", get_for_feature)
    monkeypatch.setattr(driver, "get_client", lambda p: client)


async def _listing(url):
    if url.endswith("_series_matrix.txt.gz"):
        return _SERIES_MATRIX
    if url.endswith("filelist.txt"):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    return _LISTING


async def _stream(url, max_bytes):
    text = _MATRIX_TEXT if url.endswith(_MATRIX) else _TABLE_TEXT
    return gzip.compress(text.encode())[:max_bytes]


async def _bytes(url):
    if url == _BASE + _MATRIX:
        return gzip.compress(_MATRIX_TEXT.encode())
    if url == _BASE + _TABLE:
        return gzip.compress(_TABLE_TEXT.encode())
    request = httpx.Request("GET", url)
    raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))


class _Storage:
    def __init__(self):
        self.files = {}

    async def write_text(self, uri, text, *, content_type="text/plain"):
        self.files[uri] = text

    async def read_text(self, uri, *, encoding="utf-8"):
        return self.files[uri]


async def _stage2_study(session, admin_user):
    from app.models.template_notebook import TemplateNotebook

    org = await session.get(Organization, admin_user.organization_id)
    org.lit_validation_autonomy = "autonomous"
    for path in ("notebooks/de_normalized_limma.ipynb", "notebooks/de_bulk_deseq2.ipynb"):
        session.add(TemplateNotebook(organization_id=admin_user.organization_id, name=path, category="differential_expression",
                                     notebook_path=path, parameters_json={}, is_builtin=True))
    study = ValidationStudy(
        organization_id=admin_user.organization_id, requested_by_user_id=admin_user.id, source_accession="GSE555001",
        intended_route="deposit", state="acquiring_processed",
        evidence_json={"route": "deposit", "assessment": {"at": "x"}},
    )
    session.add(study)
    await session.flush()
    predicate = {"significance": {"kind": "pvalue", "operator": "<", "value": 0.01, "adjustment": None},
                 "effect": {"kind": "none"}, "direction": "up",
                 "count": {"relation": "=", "value": 1.0, "tolerance": None, "entity": "gene"}, "status": "resolved"}
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, accessions=["GSE555001"], pipeline_key="nf-core/rnaseq",
        differential_design={
            "contrasts": [{"name": "KO vs WT", "assay": "bulk RNA-seq", "test_condition": "SAMD1 KO",
                           "reference_condition": "WT", "test_samples": [], "reference_samples": [], "cutoffs": _P}],
            "selected_contrast": {"contrast_index": 0, "decided_by": "claim_selection"},
        },
        reported_experiments=[{"id": "e2", "assay": "bulk RNA-seq", "status": "extracted", "workflow": "nf-core/rnaseq",
                               "reference": {"assembly": {"status": "usable", "resolved": "GRCm38"},
                                             "annotation": {"status": "unavailable", "stated": "GENCODE M23"}}}],
        analysis_selection={"current": {"revision": 1, "reported_experiment_id": "e2", "claim_index": 0,
                                        "check": "processed_reanalysis", "contrast_index": 0, "workflow": "nf-core/rnaseq",
                                        "predicate": predicate, "predicate_words": "SAMD1 KO versus WT, P < 0.01, up",
                                        "decided_by": "model", "reason": "the authors' table", "confidence": 0.8},
                            "history": [], "candidates": [], "unassessed": []},
    )
    await ReproductionPlanService.add_comparison_targets(
        session, plan, [{"metric_key": "", "claim_text": "1 gene was up in SAMD1 KO (P < 0.01)", "claimed_value": 1.0,
                         "contrast_index": 0, "reported_experiment_id": "e2", "cutoffs": _P, "direction": "up"}],
    )
    await session.flush()
    return study


async def _acquire(session, study, storage):
    await ValidationDriverService._handle_acquiring_processed(
        session, study, fetcher=_bytes, storage_adapter=storage, inventory_fetcher=_listing, stream=_stream
    )


@pytest.mark.asyncio
async def test_the_input_its_mapping_and_the_authors_table_are_chosen_together_and_acquired(session, admin_user, monkeypatch):
    client = _Client({"primary_matrix": _MATRIX, "author_table": _TABLE, "mapping": _mapping(), "reason": "day 0 ESC",
                      "confidence": 0.8})
    _patch(monkeypatch, client)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage)

    evidence = study.evidence_json
    assert evidence["input_choice"]["primary_matrix"] == _MATRIX
    assert evidence["input_choice"]["author_table"] == _TABLE
    assert evidence["input_choice"]["previews"]  # the decision saw the files, bounded
    kinds = {f["filename"]: f["artifact_type"] for f in evidence["deposit"]["files"]}
    assert kinds == {_MATRIX: "deposited_matrix", _TABLE: "deposited_result_table"}
    plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
    current = plan.analysis_selection_json["current"]
    assert current["revision"] == 2
    assert current["input"]["matrix"] == _MATRIX
    assert current["reason"] == "the authors' table"  # the claim's own decision is kept
    assert study.state == "inspecting_deposit"


@pytest.mark.asyncio
async def test_a_validated_mapping_rewrites_the_contrast_and_the_comparison_needs_no_person(session, admin_user, monkeypatch):
    client = _Client({"primary_matrix": _MATRIX, "author_table": _TABLE, "mapping": _mapping(), "reason": "r"})
    _patch(monkeypatch, client)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage)
    await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=storage)

    plan = await ReproductionPlanService.get_plan(session, study.id, study.organization_id)
    contrast = plan.differential_design_json["contrasts"][0]
    assert contrast["test_samples"] == ["KO_c5", "KO_c16"]
    assert contrast["reference_samples"] == ["WT_a", "WT_b"]
    assert contrast["units"]["KO_c16"] == "c16"
    level3 = study.evidence_json.get("level3")
    assert level3 is not None, study.evidence_json.get("level3_skipped")
    assert level3["ground_truth"]["source"] == "identified_author_table"
    assert {e["id"] for e in level3["paper_finding_set"]["entities"]} == {"g1", "g2"}
    # change_7.5 section 4.1: the claim checked against the authors' table itself. Its header names
    # no ratio, so a directional claim is unresolved with both readings shown.
    [record] = study.evidence_json["author_consistency"]["records"]
    assert record["claim_index"] == 0
    assert record["outcome"] == "unresolved"
    assert [c["count"] for c in record["candidates"]] == [1, 1]


@pytest.mark.asyncio
async def test_an_unresolved_mapping_holds_keeps_the_input_and_resume_re_enters_mapping(session, admin_user, monkeypatch):
    client = _Client(
        {"primary_matrix": _MATRIX, "author_table": _TABLE, "mapping": _mapping(ko16_quote="clone: c99"), "reason": "r"},
        {"mapping": _mapping(), "reason": "read again", "confidence": 0.7},
    )
    _patch(monkeypatch, client)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage)
    await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=storage)

    evidence = study.evidence_json
    assert study.state == "classified"
    assert evidence["deposit_failed"]["cause"] == "sample_mapping_unresolved"
    assert "clone: c99" in evidence["deposit_failed"]["reason"]
    assert evidence["input_choice"]["mapping"][3]["evidence"][0]["quote"] == "clone: c99"  # the proposal is kept
    assert "acquisition_retry_at" not in evidence

    downloads = []

    async def counted(url):
        downloads.append(url)
        return await _bytes(url)

    study = await ValidationStudyService.resume_study(session, study.id, study.organization_id, admin_user.id)
    await ValidationStudyService.approve_plan(session, study.id, study.organization_id, admin_user.id, route="deposit")
    study.evidence_json = {**study.evidence_json, "assessment": {"at": "x"}}
    await ValidationDriverService._handle_acquiring_processed(
        session, study, fetcher=counted, storage_adapter=storage, inventory_fetcher=_listing, stream=_stream
    )
    assert study.state == "inspecting_deposit"
    assert downloads == []  # the same input: never re-retrieved
    await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=storage)
    assert study.evidence_json["input_choice"]["mapping_validation"]["status"] == "accepted"
    assert study.evidence_json.get("level3") is not None


@pytest.mark.asyncio
async def test_the_chosen_input_is_re_checked_against_the_selected_contrast(session, admin_user, monkeypatch):
    """7.4 section 2.3, carried: after the choice, the input's own samples are checked against the
    contrast. A ChIP-seq matrix never serves an RNA-seq contrast, however its columns map."""
    client = _Client({"primary_matrix": _MATRIX, "author_table": _TABLE, "mapping": _mapping(), "reason": "r"})
    _patch(monkeypatch, client)
    study = await _stage2_study(session, admin_user)
    storage = _Storage()
    await _acquire(session, study, storage)
    study.evidence_json = {
        **study.evidence_json,
        "sample_manifest": [{**r, "library_strategy": "ChIP-Seq"} for r in study.evidence_json["sample_manifest"]],
    }
    await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=storage)
    assert study.evidence_json["deposit_failed"]["cause"] == "no_compatible_contrast"
    assert "ChIP-Seq" in study.evidence_json["deposit_failed"]["reason"]
