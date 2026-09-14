"""plan_8_2 section 1.1 acceptance: no comparison uses a table that is not bound to the claim's contrast.

- Groff's sex-comparison table cannot assess the aneuploidy, morphology or morphokinetic claims.
- A single wrong table is rejected, and a correctly bound single table is accepted.
- SAMD1's two experiments cannot cross-match through their shared gene name.
- A multi-contrast table works only through its per-contrast columns.
- Old supplement evidence without an established binding cannot bypass these checks.

Supplement checking at retrieval, the queued consistency check and the legacy projection all go through
the one contract. Paper-shaped fixtures identify evidence; nothing here is a rule about either paper.
"""

import gzip
import io
import pathlib
import zipfile

import pytest

from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.supplement_inventory import parse_jats_supplements, resolve_supplements
from app.services.validation_author_consistency import claim_predicates
from app.services.validation_study_service import ValidationStudyService

_GROFF = pathlib.Path(__file__).parent / "fixtures" / "groff"
_SAMD1 = pathlib.Path(__file__).parent / "fixtures" / "samd1"
_S3 = "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt"
_PADJ = [{"kind": "padj", "operator": "<", "value": 0.05}]

_GROFF_CONTRASTS = [
    {"name": "aneuploid vs euploid WE", "test_condition": "aneuploid WE", "reference_condition": "euploid WE"},
    {"name": "XX vs XY WE", "test_condition": "XX WE", "reference_condition": "XY WE"},
    {
        "name": "good morphology (AA) vs poor morphology (CC) WE",
        "test_condition": "AA morphology WE",
        "reference_condition": "CC morphology WE",
    },
    {
        "name": "high vs low morphokinetic quality",
        "test_condition": "high morphokinetic quality embryos",
        "reference_condition": "low morphokinetic quality embryos",
    },
]


def _count(value, index, cutoffs=None, direction=None):
    return {
        "claim_text": f"{value} genes",
        "claimed_value": value,
        "output_type": "gene_set_size",
        "direction": direction,
        "contrast_index": index,
        "cutoffs": cutoffs if cutoffs is not None else _PADJ,
        "count_relation": "=",
    }


# The sex comparison's claims, then the three contrasts the table does not report.
_GROFF_CLAIMS = [_count(194, 1), _count(53, 0), _count(10, 2), _count(9, 3)]


class _Plan:
    def __init__(self, contrasts):
        self.differential_design_json = {"contrasts": contrasts}


def _bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(_S3, (_GROFF / "supplemental_file_3_siggenes.txt").read_bytes())
    return buf.getvalue()


async def _groff_supplements():
    async def fetch(url):
        return _bundle()

    return await resolve_supplements(
        "PMC0000001",
        parse_jats_supplements((_GROFF / "fulltext_jats.xml").read_text()),
        fetcher=fetch,
        predicates=claim_predicates([dict(c) for c in _GROFF_CLAIMS], _Plan(_GROFF_CONTRASTS)),
    )


def _s3_row(rows):
    return next(r for r in rows if r.get("filename") == _S3)


class TestGroffsSexComparisonTable:
    @pytest.mark.asyncio
    async def test_at_retrieval_it_is_compared_only_with_the_claims_its_contrast_reports(self):
        records = {r["claim_index"]: r for r in _s3_row(await _groff_supplements())["consistency"]}
        assert records[0]["binding"]["status"] == "established"
        for index in (1, 2, 3):
            assert records[index]["binding"]["status"] == "rejected"
            assert records[index]["outcome"] == "unresolved"
            assert records[index]["rows_passing"] is None
        assert "disagree" not in {r["outcome"] for r in records.values()}

    @pytest.mark.asyncio
    async def test_the_queue_never_reuses_it_for_another_contrasts_claim(self, session, admin_user):
        study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
        study.state = "classified"
        plan = await ReproductionPlanService.create_plan(
            session,
            study,
            admin_user.id,
            accessions=["EGAS00000000001"],
            differential_design={"contrasts": [{**c, "reported_experiment_id": "e1"} for c in _GROFF_CONTRASTS]},
            reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1, 2, 3]}],
        )
        targets = await ReproductionPlanService.add_comparison_targets(
            session, plan, [{**c, "metric_key": "", "reported_experiment_id": "e1"} for c in _GROFF_CLAIMS]
        )
        study.evidence_json = {"supplements": await _groff_supplements()}
        await session.flush()
        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=_refuse)
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        sex = records[targets[0].id]
        assert sex.state == queue.DONE and sex.outcome_json["binding"]["status"] == "established"
        for target in targets[1:]:
            record = records[target.id]
            assert record.state == queue.UNRESOLVED and record.terminal_reason == queue.BINDING
            assert (record.outcome_json or {}).get("outcome") != "disagree"
            assert "XX vs XY WE" in record.outcome_json["reason"]


async def _refuse(url):
    raise AssertionError(f"nothing is downloaded: {url}")


_ACCESSION = "GSE999001"
_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE999nnn/GSE999001/suppl/"


async def _deposit_study(session, user, *, contrasts, experiments, claims, tables, input_table=None, selected=None):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    study.state = "plan_ready"
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=[_ACCESSION],
        differential_design={"contrasts": contrasts},
        reported_experiments=experiments,
        resources=[
            {
                "identifier": _ACCESSION,
                "type": "sequencing_data",
                "reported_experiment_ids": [e["id"] for e in experiments],
            }
        ],
        analysis_selection={"current": {"contrast_index": selected}} if selected is not None else None,
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {**c, "metric_key": "", "reported_experiment_id": contrasts[c["contrast_index"]]["reported_experiment_id"]}
            for c in claims
        ],
    )
    evidence = {
        "capabilities": {"deposits": [{"accession": _ACCESSION, "archive": "geo", "result_tables": list(tables)}]}
    }
    if input_table:
        evidence["deposit_selection"] = {"author_table": input_table}
    study.evidence_json = evidence
    await session.flush()
    return study, plan, targets


class _Fetcher:
    def __init__(self, blobs):
        self.blobs = blobs
        self.urls = []

    async def __call__(self, url):
        self.urls.append(url)
        return self.blobs[url]


_SAMD1_CONTRASTS = [
    {
        "name": "SAMD1 KO vs WT (undifferentiated ES cells)",
        "test_condition": "SAMD1 KO mouse ES cells",
        "reference_condition": "WT mouse ES cells",
        "reported_experiment_id": "e2",
    },
    {
        "name": "SAMD1 KO vs WT (day 7 differentiation)",
        "test_condition": "SAMD1 KO mouse ES cells day 7",
        "reference_condition": "WT mouse ES cells day 7",
        "reported_experiment_id": "e3",
    },
]
_SAMD1_TABLES = [
    "GSE999001_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz",
    "GSE999001_DeSeq2-KDM1AKOvsWT.txt.gz",
    "GSE999001_RNA-Seq_DeSeq2.txt.gz",
]
_P01 = [{"kind": "pvalue", "operator": "<", "value": 0.01}]


class TestTwoExperimentsSharingAGeneName:
    @pytest.mark.asyncio
    async def test_the_undifferentiated_claims_never_bind_the_differentiation_table(self, session, admin_user):
        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_SAMD1_CONTRASTS,
            experiments=[
                {"id": "e2", "assay": "bulk RNA-seq", "claim_indices": [0, 1]},
                {"id": "e3", "assay": "bulk RNA-seq", "claim_indices": [2]},
            ],
            claims=[_count(257, 0, _P01, "up"), _count(524, 0, _P01, "down"), _count(5904, 1, _P01)],
            tables=_SAMD1_TABLES,
            input_table="GSE999001_RNA-Seq_DeSeq2.txt.gz",
            selected=0,
        )
        records = {r.comparison_target_id: r for r in await consistency.enqueue(session, study, plan)}
        for target in targets[:2]:
            deps = records[target.id].dependencies_json
            assert deps["table"]["name"] == "GSE999001_RNA-Seq_DeSeq2.txt.gz"
            rejected = {c["name"]: c["status"] for c in deps["bindings"]}
            assert rejected["GSE999001_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz"] == "rejected"

        headerless = gzip.compress(b"Samd1\t1.5\t-3.2\t0.001\t1.1\t0.0001\t0.001\n")
        fetch = _Fetcher(
            {
                _BASE + "GSE999001_RNA-Seq_DeSeq2.txt.gz": (_SAMD1 / "GSE144396_RNA-Seq_DeSeq2.txt.gz").read_bytes(),
                _BASE + "GSE999001_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz": headerless,
            }
        )
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        await session.flush()
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        for target in targets[:2]:
            record = records[target.id]
            # The UTF-16 table decodes, and its columns do not name the contrast: a candidate stays unresolved.
            assert record.state == queue.UNRESOLVED and record.terminal_reason == queue.BINDING
            assert record.attempts_json[-1]["url"].endswith("GSE999001_RNA-Seq_DeSeq2.txt.gz")
            assert record.attempts_json[-1]["decoding"]["encoding"] == "utf-16-le"
            assert record.outcome_json["binding"]["status"] == "candidate"
        # Its own name-linked table is read for its columns, and a headerless table establishes nothing.
        differentiation = records[targets[2].id]
        assert differentiation.state == queue.UNRESOLVED and differentiation.terminal_reason == queue.BINDING
        assert differentiation.outcome_json["binding"]["status"] == "candidate"


_ONE_CONTRAST = [
    {
        "name": "treated vs control",
        "test_condition": "treated",
        "reference_condition": "control",
        "reported_experiment_id": "e1",
    },
    {
        "name": "infected vs mock",
        "test_condition": "infected",
        "reference_condition": "mock",
        "reported_experiment_id": "e1",
    },
]


def _table(header_lfc: str, up: int) -> bytes:
    rows = [f"gene\t{header_lfc}\tpvalue\tpadj"] + [f"u{i}\t2.0\t0.001\t0.01" for i in range(up)] + ["n\t0.1\t0.5\t0.9"]
    return gzip.compress("\n".join(rows).encode())


class TestASingleTable:
    @pytest.mark.asyncio
    async def test_a_correctly_bound_single_table_is_compared(self, session, admin_user):
        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_ONE_CONTRAST[:1],
            experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0]}],
            claims=[_count(3, 0, _P01, "up")],
            tables=["GSE999001_results.txt.gz"],
        )
        await consistency.enqueue(session, study, plan)
        fetch = _Fetcher({_BASE + "GSE999001_results.txt.gz": _table("log2FoldChange(treated/control)", 3)})
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        (record,) = await queue.records_for(session, study.id)
        assert record.state == queue.DONE and record.outcome_json["outcome"] == "agree"
        assert record.outcome_json["binding"]["evidence"][0]["kind"] == "columns"

    @pytest.mark.asyncio
    async def test_a_single_table_for_another_contrast_is_rejected_without_being_downloaded(self, session, admin_user):
        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_ONE_CONTRAST,
            experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1]}],
            claims=[_count(3, 0, _P01, "up")],
            tables=["GSE999001_infected_vs_mock.txt.gz"],
        )
        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=_refuse)
        (record,) = await queue.records_for(session, study.id)
        assert record.state == queue.UNRESOLVED and record.terminal_reason == queue.BINDING
        assert "infected vs mock" in record.outcome_json["reason"]


_POOLED = [
    {"name": "KO vs WT", "test_condition": "KO", "reference_condition": "WT", "reported_experiment_id": "e1"},
    {"name": "DKO vs WT", "test_condition": "DKO", "reference_condition": "WT", "reported_experiment_id": "e1"},
]


class TestAPooledTable:
    @pytest.mark.asyncio
    async def test_each_contrast_is_read_from_its_own_columns(self, session, admin_user):
        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_POOLED,
            experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1]}],
            claims=[_count(2, 0, _P01, "up"), _count(1, 1, _P01, "up")],
            tables=["GSE999001_all_contrasts.txt.gz"],
        )
        rows = ["gene\tKO_vs_WT_log2FC\tKO_vs_WT_pvalue\tDKO_vs_WT_log2FC\tDKO_vs_WT_pvalue"]
        rows += ["g1\t2.0\t0.001\t2.0\t0.001", "g2\t2.0\t0.001\t0.1\t0.5", "g3\t0.1\t0.5\t0.1\t0.5"]
        fetch = _Fetcher({_BASE + "GSE999001_all_contrasts.txt.gz": gzip.compress("\n".join(rows).encode())})
        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=fetch)
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        ko, dko = records[targets[0].id], records[targets[1].id]
        assert (ko.outcome_json["outcome"], ko.outcome_json["rows_passing"]) == ("agree", 2)
        assert (dko.outcome_json["outcome"], dko.outcome_json["rows_passing"]) == ("agree", 1)
        assert ko.outcome_json["columns"]["lfc"] == "KO_vs_WT_log2FC"
        assert dko.outcome_json["columns"]["lfc"] == "DKO_vs_WT_log2FC"


class TestOldEvidenceCannotBypassTheBinding:
    @pytest.mark.asyncio
    async def test_a_held_supplement_comparison_without_a_binding_is_not_reused(self, session, admin_user):
        study, plan, targets = await _deposit_study(
            session,
            admin_user,
            contrasts=_ONE_CONTRAST[:1],
            experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0]}],
            claims=[_count(3, 0, _P01, "up")],
            tables=[],
        )
        held = {
            "claim_index": 0,
            "outcome": "disagree",
            "table": "S3.txt",
            "rows_passing": 194,
            "reason": "194 against 3",
        }
        study.evidence_json = {
            **study.evidence_json,
            "supplements": [
                {
                    "filename": "S3.txt",
                    "label": "Supplemental File S3",
                    "role": "results_table",
                    "resolved": True,
                    "consistency": [held],
                }
            ],
        }
        await session.flush()
        await consistency.enqueue(session, study, plan)
        await consistency.run_pending(session, study, plan, fetcher=_refuse)
        (record,) = await queue.records_for(session, study.id)
        assert record.state == queue.UNRESOLVED
        assert record.outcome_json["outcome"] != "disagree"
        assert record.outcome_json["superseded"]["outcome"] == "disagree"

    def test_the_legacy_projection_shows_an_unbound_comparison_as_pending_re_evaluation(self):
        from app.services.validation_report_summary import summarize

        claim = {**_count(53, 0), "id": 1}
        held = {
            "claim_index": 0,
            "outcome": "disagree",
            "table": "S3.txt",
            "rows_passing": 194,
            "reason": "194 against 53",
        }
        evidence = {
            "supplements": [{"filename": "S3.txt", "role": "results_table", "resolved": True, "consistency": [held]}]
        }
        plan = {"differential_design": {"contrasts": _GROFF_CONTRASTS[:1]}}
        summary = summarize(study={"state": "classified"}, evidence=evidence, plan=plan, targets=[claim], issues=[])
        row = summary["claims"][0]["consistency"]
        assert row["outcome"] == "pending_re_evaluation"
        assert row["superseded"]["outcome"] == "disagree"
        assert "pending re-evaluation" in row["label"].lower()

    def test_a_check_record_made_before_bindings_is_pending_re_evaluation_in_the_report(self):
        from app.services.validation_report_summary import summarize

        claim = {**_count(53, 0), "id": 7}
        record = {
            "kind": "author_results",
            "comparison_target_id": 7,
            "state": "done",
            "revision": 1,
            "dependencies": {"table": {"name": "S3.txt", "source": "supplement"}, "identified_by": "only_table"},
            "outcome": {"outcome": "disagree", "table": "S3.txt", "rows_passing": 194, "reason": "194 against 53"},
        }
        plan = {"differential_design": {"contrasts": _GROFF_CONTRASTS[:1]}}
        summary = summarize(
            study={"state": "classified"}, evidence={}, plan=plan, targets=[claim], issues=[], checks=[record]
        )
        row = summary["claims"][0]["consistency"]
        assert row["outcome"] == "pending_re_evaluation"
        assert row["superseded"]["outcome"] == "disagree"


def test_the_markdown_export_keeps_the_superseded_comparison_and_never_as_current_evidence():
    from app.services.provenance.markdown_renderer import _append_each_claim
    from app.services.validation_report_summary import summarize

    claim = {**_count(53, 0), "id": 1, "checks": {"author_results": {"status": "available", "reason": None}}}
    held = {"claim_index": 0, "outcome": "disagree", "table": "S3.txt", "rows_tested": 194, "rows_passing": 194}
    evidence = {
        "supplements": [{"filename": "S3.txt", "role": "results_table", "resolved": True, "consistency": [held]}]
    }
    plan = {"differential_design": {"contrasts": _GROFF_CONTRASTS[:1]}}
    summary = summarize(study={"state": "classified"}, evidence=evidence, plan=plan, targets=[claim], issues=[])
    parts: list[str] = []
    _append_each_claim(parts, summary)
    text = "\n".join(parts)
    assert "Pending re-evaluation against the authors' results (S3.txt)" in text
    assert "Superseded comparison, not current evidence: Differs from the authors' results" in text
    assert "194 of 194 rows pass" in text.split("Superseded comparison")[1]
