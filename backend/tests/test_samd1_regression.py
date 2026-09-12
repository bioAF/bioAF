"""change_7.4 section 1.7: SAMD1 (study 37) as a regression fixture, and the survival test.

Study 37 (10.1126/sciadv.abf2229, GEO GSE144396) chose the ChIP-seq workflow for an expression claim,
grouped the deposited samples wrongly, required day-7 samples the selected matrix never held, lost the
paper's P < 0.01 on the way to execution, and ended with a false sentence: "bioAF could not reach the
deposit after 3 attempts".

SAMD1 exposes bioAF's defects; it is not a specification. Nothing here reaches production behaviour:
no identifier, filename, column name, count, cutoff or label from it is used by the code under test.

**The survival test** feeds a hand-written, known-correct structured extraction through the real
code, parse to author-table normalization, with only the contrast selection stubbed with the correct
answer, and asserts that the cutoff, its operator, the claims' directions and the selected contrast
arrive unchanged at every boundary, that no default is injected anywhere, and that the run ends with
an accurate cause rather than a retry.
"""

import json
import pathlib
from types import SimpleNamespace

import httpx
import pytest

from app.models.validation_study import ValidationStudy
from app.services import validation_extraction_service as ext
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "samd1"
_STUDY_37 = json.loads((_FIXTURES / "study_37_persisted.json").read_text())
_HEADER = (_FIXTURES / "normalized_counts_header.tsv").read_text().rstrip("\n")

_P_001 = [{"kind": "pvalue", "operator": "<", "value": 0.01}]

# What a correct reading of the paper returns. The assay words, arms and cutoffs are the paper's; the
# threshold kind is `pvalue`, which the binding vocabulary could not say before this change.
_CORRECT_EXTRACTION = {
    "accessions": ["GSE144396"],
    "sample_structure": {"organism": "Mus musculus"},
    "method": {"assay": "ChIP-seq and bulk RNA-seq", "tools": ["DESeq2", "MACS2"], "reference_build": "mm9"},
    "differential_design": {
        "contrasts": [
            {
                "name": "SAMD1 KO vs WT (undifferentiated ES cells)",
                "assay": "bulk RNA-seq",
                "test_condition": "SAMD1 KO",
                "reference_condition": "WT",
                "test_samples": [],
                "reference_samples": [],
                "finding_claim_index": 0,
                "thresholds": {"log2fc": None, "padj": None},
            },
            {
                "name": "SAMD1 KO vs WT (day 7 differentiation)",
                "assay": "bulk RNA-seq",
                "test_condition": "SAMD1 KO day 7",
                "reference_condition": "WT day 7",
                "test_samples": [],
                "reference_samples": [],
                "finding_claim_index": 2,
                "thresholds": {"log2fc": None, "padj": None},
            },
        ],
        "thresholds": {"log2fc": None, "padj": None},
    },
    "claims": [
        {
            "metric_key": "",
            "claim_text": "This experiment identified 524 significantly (P < 0.01) down-regulated and 257 up-regulated genes",
            "value": 257,
            "unit": "genes",
            "direction": "up",
            "output_type": "gene_set_size",
            "contrast": "SAMD1 KO vs WT (undifferentiated ES cells)",
            "threshold": 0.01,
            "threshold_kind": "pvalue",
            "cutoffs": _P_001,
        },
        {
            "metric_key": "",
            "claim_text": "This experiment identified 524 significantly (P < 0.01) down-regulated and 257 up-regulated genes",
            "value": 524,
            "unit": "genes",
            "direction": "down",
            "output_type": "gene_set_size",
            "contrast": "SAMD1 KO vs WT (undifferentiated ES cells)",
            "threshold": 0.01,
            "threshold_kind": "pvalue",
            "cutoffs": _P_001,
        },
        {
            "metric_key": "",
            "claim_text": "We identified more than 5000 genes of which the expression changes significantly (P < 0.01)",
            "value": 5000,
            "unit": "genes",
            "output_type": "gene_set_size",
            "contrast": "SAMD1 KO vs WT (day 7 differentiation)",
            "threshold": 0.01,
            "threshold_kind": "pvalue",
            "cutoffs": _P_001,
        },
        {
            "metric_key": "peak_count",
            "claim_text": "we identified 8733 significant peaks",
            "value": 8733,
            "unit": "peaks",
            "output_type": "count",
        },
    ],
    "data_availability": "deposited",
    "blockers": [],
}

_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE144nnn/GSE144396/suppl/"
_MATRIX = "GSE144396_RNA-Seq_NormalizedCounts.txt.gz"
# The deposit's real header, with synthetic normalized values: the fixture holds no deposited data.
_MATRIX_TEXT = (
    _HEADER
    + "\n"
    + "\n".join(f"gene{i}\t" + "\t".join(f"{(i * 13 + j * 7) % 97 + 0.5:.3f}" for j in range(8)) for i in range(6))
)
_LISTING = f'<html><body><a href="{_MATRIX}">{_MATRIX}</a><a href="GSE144396_RAW.tar">x</a></body></html>'


def _extraction_response() -> str:
    return "```json\n" + json.dumps(_CORRECT_EXTRACTION) + "\n```"


def _patch_models(monkeypatch, *, selection: dict | None = None):
    """The reading returns the correct extraction and binding declines every metric (they are DE
    counts). Contrast selection is the only decision stubbed, with the answer the test names."""
    responses = {
        ext.PAPER_READING_INTENT: _extraction_response(),
        ext.CLAIM_BINDING_INTENT: "```json\n"
        + json.dumps(
            {
                "bindings": [
                    {
                        "claim_index": i,
                        "bound_key": ("peak_count" if i == 3 else None),
                        "reason": "r",
                        "confidence": 0.9,
                    }
                    for i in range(4)
                ]
            }
        )
        + "\n```",
    }

    async def fake_decide(*, intent, system, payload, client, model, api_key, allowed=None, **kwargs):
        from app.services.llm_decision import OUTCOME_OK, Decision

        return Decision(outcome=OUTCOME_OK, text=responses[intent], intent=intent, model=model)

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None)

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None)

    monkeypatch.setattr(ext, "decide", fake_decide)
    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(ext, "get_client", lambda p: object())
    if selection is not None:

        async def fake_select(contrasts, **kwargs):
            return dict(selection)

        monkeypatch.setattr(ext, "select_contrast", fake_select)


async def _read(session, admin_user, monkeypatch, **kwargs):
    _patch_models(monkeypatch, **kwargs)
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_doi=_STUDY_37["study"]["source_doi"],
        intended_route="deposit",
    )
    await session.flush()
    await ext.ValidationExtractionService.extract(session, study, "TEXT", admin_user.organization_id, admin_user.id)
    await session.flush()
    plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
    return study, plan


def _no_default_anywhere(obj) -> None:
    """No 0.05 adjusted P and no 1.0 fold change appeared that the paper did not state."""
    text = json.dumps(obj)
    assert '"padj": 0.05' not in text
    assert '"padj_threshold": 0.05' not in text
    assert '"lfc_threshold": 1.0' not in text


# ---- the defects the owner's review reproduced without a model call ----


class TestWhatStudy37Reproduced:
    def test_a_compound_assay_is_no_longer_read_as_whichever_route_comes_first(self):
        """`map_method` still answers nf-core/chipseq for both orders (the mapper is unchanged, as the
        locked decision requires). What changed is that a contrast's compound assay is not taken to
        be either one."""
        from app.services.contrast_selection import UNSTATED, contrast_compatibility
        from app.services.pipeline_mapper import map_method, pipelines_named_by

        assert map_method("ChIP-seq and bulk RNA-seq").pipeline_key == "nf-core/chipseq"
        assert set(pipelines_named_by("bulk RNA-seq and ChIP-seq")) == {"nf-core/chipseq", "nf-core/rnaseq"}
        status, _ = contrast_compatibility({"assay": "ChIP-seq and bulk RNA-seq"}, pipeline_key="nf-core/chipseq")
        assert status == UNSTATED

    def test_the_p_value_is_carried_onto_the_contrast(self):
        from app.services.validation_claim_cutoffs import derive_contrast_thresholds

        [contrast, _] = derive_contrast_thresholds(
            [dict(c) for c in _CORRECT_EXTRACTION["differential_design"]["contrasts"]], _CORRECT_EXTRACTION["claims"]
        )
        assert contrast["cutoffs"] == _P_001

    def test_columns_grouped_by_their_names_are_an_unresolved_mapping_never_an_absence(self):
        from app.services.deposit_metadata_association import associate_columns, empty_arm_cause

        associations = associate_columns(_STUDY_37["evidence"]["deposit_inspection"]["columns"])
        assert {a["source"] for a in associations} == {"column_name"}
        assert empty_arm_cause(associations) == "sample_mapping_unresolved"

    def test_the_persisted_rewrite_hold_would_have_been_read_as_transient(self):
        """Why study 37 was retried: its sentence matched no signature. The driver now passes the
        cause, so nothing depends on this reading any more."""
        from app.services.validation_acquisition_outcome import TRANSIENT, classify_hold

        assert classify_hold(_STUDY_37["evidence"]["deposit_failed"]["reason"]).kind == TRANSIENT


class TestStudy37ReplayedOnTheCurrentBuild:
    @pytest.mark.asyncio
    async def test_it_ends_with_no_compatible_contrast_before_acquiring_anything(self, session, admin_user):
        """Until stage 2 the workflow is still chosen first, so a ChIP-seq plan with two RNA-seq
        contrasts ends with `no_compatible_contrast`. That is accurate for stage 1."""
        record = _STUDY_37["reproduction_plan"]
        study = ValidationStudy(
            organization_id=admin_user.organization_id,
            requested_by_user_id=admin_user.id,
            source_doi=_STUDY_37["study"]["source_doi"],
            intended_route="deposit",
            state="acquiring_processed",
            evidence_json={
                "route": "deposit",
                "capabilities": _STUDY_37["evidence"]["capabilities"],
                "supplements": _STUDY_37["evidence"]["supplements"],
                "pmcid": _STUDY_37["evidence"]["pmcid"],
                "assessment": {"at": "x"},
            },
        )
        session.add(study)
        await session.flush()
        await ReproductionPlanService.create_plan(
            session,
            study,
            admin_user.id,
            accessions=record["accessions"],
            pipeline_key=record["pipeline_key"],
            differential_design=record["differential_design"],
        )
        await session.flush()
        listed: list[str] = []

        async def listing(url):
            listed.append(url)
            return _LISTING

        await ValidationDriverService._handle_acquiring_processed(session, study, inventory_fetcher=listing)

        evidence = study.evidence_json
        assert listed == []
        assert evidence["deposit_failed"]["cause"] == "no_compatible_contrast"
        assert "acquisition_retry_at" not in evidence
        assert "could not reach" not in (study.failure_reason or "")
        assert study.classification == "inconclusive"
        kinds = {limitation["kind"] for limitation in evidence["completion"]["limitations"]}
        assert "no_compatible_contrast" in kinds
        assert "failed_discovery" not in kinds


# ---- the survival test ----


class TestCorrectStructuredInputSurvivesTheRealCode:
    @pytest.mark.asyncio
    async def test_the_cutoff_and_the_directions_survive_the_plan(self, session, admin_user, monkeypatch):
        _, plan = await _read(session, admin_user, monkeypatch)

        targets = sorted(plan.comparison_targets, key=lambda t: t.id)
        assert [t.direction for t in targets[:2]] == ["up", "down"]
        assert all(t.cutoffs == _P_001 for t in targets[:3])
        assert all(t.threshold_kind == "pvalue" for t in targets[:3])
        assert all(t.unresolved_reason is None for t in targets)
        contrasts = plan.differential_design_json["contrasts"]
        assert contrasts[0]["cutoffs"] == _P_001
        assert contrasts[1]["cutoffs"] == _P_001
        _no_default_anywhere(plan.differential_design_json)

    @pytest.mark.asyncio
    async def test_the_chipseq_workflow_selects_no_contrast_and_says_why(self, session, admin_user, monkeypatch):
        """No selection stub: the check settles it without a model."""
        _, plan = await _read(session, admin_user, monkeypatch)

        assert plan.pipeline_key == "nf-core/chipseq"
        selection = plan.differential_design_json["selected_contrast"]
        assert selection["contrast_index"] is None
        assert selection["outcome"] == "no_compatible_contrast"
        assert "nf-core/chipseq" in selection["reason"]

    @pytest.mark.asyncio
    async def test_on_an_expression_workflow_the_selected_contrast_reaches_every_boundary(
        self, session, admin_user, monkeypatch
    ):
        """The stage 2 shape, forced here: the expression workflow and the correct contrast. The run
        goes through acquisition, inspection, association and the rewrite on the real code, and ends
        with the accurate cause (the columns carry no identifier that places them in SAMD1 KO or WT),
        not with a retry."""
        study, plan = await _read(
            session,
            admin_user,
            monkeypatch,
            selection={"contrast_index": 0, "decided_by": "model", "reason": "the ES cell expression contrast"},
        )
        plan.pipeline_key = "nf-core/rnaseq"
        await session.flush()

        async def bytes_fetch(url):
            import gzip

            if url == _BASE + _MATRIX:
                return gzip.compress(_MATRIX_TEXT.encode())
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))

        async def listing(url):
            if url.endswith("filelist.txt"):
                request = httpx.Request("GET", url)
                raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
            return _LISTING

        class _Storage:
            files: dict = {}

            async def write_text(self, uri, text, *, content_type="text/plain"):
                self.files[uri] = text

            async def read_text(self, uri, *, encoding="utf-8"):
                return self.files[uri]

        storage = _Storage()
        study.state = "acquiring_processed"
        study.evidence_json = {
            **(study.evidence_json or {}),
            "route": "deposit",
            "assessment": {"at": "x"},
            "deposit_selection": {
                "primary_matrix": _MATRIX,
                "matrix_files": [_MATRIX],
                "metadata_file": None,
                "value_type": "normalized_other",
                "decided_by": "human",
            },
            "deposit_inventory": {"accession": "GSE144396", "entries": [], "triplets": []},
        }
        await session.flush()

        await ValidationDriverService._handle_acquiring_processed(
            session, study, fetcher=bytes_fetch, storage_adapter=storage, inventory_fetcher=listing
        )
        assert study.state == "inspecting_deposit"
        await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=storage)

        evidence = study.evidence_json
        assert evidence["deposit_inspection"]["value_type_observed"] == "normalized_other"
        assert evidence["deposit_failed"]["cause"] == "sample_mapping_unresolved"
        assert "acquisition_retry_at" not in evidence
        assert _MATRIX in evidence["deposit_failed"]["reason"]
        assert "SAMD1 KO" in evidence["deposit_failed"]["reason"]
        completion = evidence["completion"]
        assert (completion["input_acquired"], completion["input_usable"], completion["ready_for_analysis"]) == (
            "yes",
            "yes",
            "no",
        )
        # The selection is the one made, and only its arms were required: the day-7 contrast is not
        # named in the refusal.
        assert "day 7" not in evidence["deposit_failed"]["reason"]
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        assert plan.differential_design_json["selected_contrast"]["contrast_index"] == 0
        _no_default_anywhere(plan.differential_design_json)

    @pytest.mark.asyncio
    async def test_the_parameter_builder_carries_the_p_value_unchanged(self, session, admin_user, monkeypatch):
        """Past the mapping (as if a person had assigned the columns), the definition reaches the
        analysis parameters unchanged, never replaced with an adjusted P.

        change_7.5 section 1.2 changed this test: it asserted the P value was refused, because the
        templates wrote only an adjusted P. They write the raw P value now, so it is carried."""
        from app.models.file import File
        from app.models.template_notebook import TemplateNotebook
        from app.services.validation_level3_service import resolve_level3_from_deposit

        study, plan = await _read(
            session, admin_user, monkeypatch, selection={"contrast_index": 0, "decided_by": "human"}
        )
        plan.pipeline_key = "nf-core/rnaseq"
        design = dict(plan.differential_design_json)
        contrasts = [dict(c) for c in design["contrasts"]]
        contrasts[0].update(test_samples=["KO Cl5 repl1", "KO Cl16"], reference_samples=["WT-1", "WT-2"])
        plan.differential_design_json = {**design, "contrasts": contrasts}
        plan.finding_claim_json = {
            "kind": "gene",
            "confirmed": True,
            "finding_set": {"kind": "gene", "entities": [{"id": "g"}]},
        }
        matrix = File(
            organization_id=admin_user.organization_id,
            filename=_MATRIX,
            storage_uri="s3://x/m.tsv",
            file_type="table",
            source_type="external_deposit",
            artifact_type="deposited_matrix",
            uploader_user_id=admin_user.id,
        )
        session.add_all(
            [
                matrix,
                TemplateNotebook(
                    organization_id=admin_user.organization_id,
                    name="limma",
                    category="differential_expression",
                    notebook_path="notebooks/de_normalized_limma.ipynb",
                    parameters_json={},
                    is_builtin=True,
                ),
            ]
        )
        await session.flush()
        evidence = {
            "deposit": {"files": [{"file_id": matrix.id, "artifact_type": "deposited_matrix"}]},
            "deposit_inspection": {"value_type_observed": "normalized_other", "id_column": ""},
        }

        decision = await resolve_level3_from_deposit(session, study, plan, evidence=evidence)

        assert decision.inputs is not None, decision.reason
        assert decision.inputs["cutoffs"]["significance"] == {"kind": "pvalue", "operator": "<", "value": 0.01}
        assert decision.inputs["cutoff_statement"].startswith("P < 0.01")

    @pytest.mark.asyncio
    async def test_the_author_table_is_read_at_the_paper_s_own_definition(self):
        """Author-table normalization applies the P value to the P-value column and nothing else."""
        from app.services.result_set_normalizer import normalize_gene_table

        table = "gene,log2FoldChange,pvalue,padj\nA,1.5,0.001,0.08\nB,-0.4,0.005,0.2\nC,2.0,0.05,0.3\n"
        fs = normalize_gene_table(table, lfc_threshold=0.0, padj_threshold=0.01, significance_kind="pvalue")
        assert {e.id: e.direction for e in fs.entities} == {"A": "up", "B": "down"}
