"""change_7.1 section 5: the whole shape of this paper, end to end.

Groff et al., *RNA-seq as a tool for evaluating human embryo competence*, `10.1101/gr.252981.119`.
The owner ran it and the study stopped at `plan_ready` with no selected finding, no execution, and
a claim-binding step reporting an unparseable model response. The report said the paper named no
accession and published no data.

Every one of those statements was false, and this fixture holds the shape that produced them:

- requested by DOI, so ``source_accession`` is NULL and the EGA accession exists only in the plan
- an EGA deposit: 54 samples, 108 FASTQ files, controlled access, no bioAF download path
- public article supplements: a metadata table, a DOCX of R code, a differential-results table
- no sample-level expression matrix anywhere, so nothing can rerun DESeq2

The assertion is not that the paper reproduces. It is that the assessment COMPLETES and every
statement in it is true.
"""

import pathlib

import pytest

from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "groff"
_JATS = (_FIXTURES / "fulltext_jats.xml").read_text()

_EGA_DATASETS = (
    '[{"accession_id":"EGAD00001005044","num_samples":54,"access_type":"controlled",'
    '"is_released":true,"is_deprecated":false}]'
)
_EGA_FILES = (
    "["
    + ",".join(f'{{"accession_id":"EGAF{i:08d}","filesize":442207842,"extension":"fastq.gz"}}' for i in range(108))
    + "]"
)

# change_7.3 section 12: the extraction study 34 actually produced, in shape. The 54 is tagged post-QC
# though the paper excludes three TE biopsies; the contrast carries the 88-gene subset's cutoff pair;
# the per-sample blocker is worded so neither consistency regex matches it. The 194 claim is here
# beside the 88 so the cutoffs mechanism is exercised; whether a model separates them is a
# model-quality question, not something a stubbed extraction can prove.
_SAMPLE_BLOCKER = (
    "Sample IDs assigned to each differential group (aneuploid/euploid, XX/XY, AA/CC, morphokinetic) are not "
    "explicitly enumerated in the text"
)
_EXTRACTION = (
    '```json\n{"accessions": ["EGAS00001003667"], '
    '"sample_structure": {"organism": "Homo sapiens", "sample_count": 54}, '
    '"method": {"assay": "bulk RNA-seq"}, '
    '"differential_design": {"contrasts": [{"name": "XX vs XY WE", "test_condition": "XX WE", '
    '"reference_condition": "XY WE", "thresholds": {"padj": 0.05, "log2fc": 2.0}, "finding_claim_index": 2}], '
    '"thresholds": {"padj": 0.05, "log2fc": null}}, '
    '"claims": [{"metric_key": "", "claim_text": "digital karyotypes were concordant with PGT-A", '
    '"source_locator": "Results"}, '
    '{"metric_key": "total_samples", "value": 54, "unit": "RNA-seq samples (35 WE + 19 TE)", '
    '"claim_text": "The resulting data set includes 35 WE samples, 19 TE biopsies", "qc_stage": "post-QC", '
    '"output_type": "count", "source_locator": "Results, Fig. 1C"}, '
    '{"metric_key": "", "value": 194, "unit": "genes", "output_type": "gene_set_size", '
    '"claim_text": "We identified 194 significantly differentially expressed genes of which 146 are sex-linked", '
    '"contrast": "XX vs XY WE", "cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}], '
    '"source_locator": "Results"}, '
    '{"metric_key": "differentially_expressed_genes", "value": 88, "unit": "genes", "output_type": "gene_set_size", '
    '"claim_text": "88 of the 194 significant genes had |log2FC| > 2", "contrast": "XX vs XY WE", '
    '"threshold": 2.0, "threshold_kind": "abs_log2fc", '
    '"cutoffs": [{"kind": "padj", "operator": "<", "value": 0.05}, {"kind": "abs_log2fc", "operator": ">", "value": 2}], '
    '"source_locator": "Results"}], '
    '"data_availability": "restricted", '
    '"code_availability": [{"kind": "supplementary", "identifier": "Supplemental File S2", "stated_in": "methods"}], '
    f'"blockers": [{{"text": "{_SAMPLE_BLOCKER}", "kind": "sample_assignment"}}]}}\n```'
)

_JUDGMENT = (
    '```json\n{"answer": "yes", "reason": "supplemental files list per-sample metadata", "confidence": 0.7}\n```'
)


class _NotFound(Exception):
    """httpx's shape for a 404: the status lives on `.response`."""

    def __init__(self):
        super().__init__("Client error '404 Not Found' for url")
        self.response = type("R", (), {"status_code": 404})()


def _docx(text: str) -> bytes:
    """A minimal real .docx holding ``text``: the stand-in for the 8.7 MB supplementary document."""
    import io
    import zipfile

    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document)
    return buffer.getvalue()


# change_7.3 section 12: the bundle as Europe PMC actually serves it, all seventeen members. The
# fixture used to hold three, so the index page, the fourth attachment and the twelve figure images
# the real bundle carries were never exercised.
_FIGURES = [f"1705f0{n}.{ext}" for n in range(1, 7) for ext in ("jpg", "gif")]


def _bundle() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, source in [
            ("supp_gr.252981.119_Supplemental_File_1_embryo_metadata.txt", "supplemental_file_1_embryo_metadata.txt"),
            ("supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx", "supplemental_file_2_allrcode.docx"),
            ("supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt", "supplemental_file_3_siggenes.txt"),
        ]:
            zf.writestr(name, (_FIXTURES / source).read_bytes())
        zf.writestr("supp_29_10_1705__index.html", "<html><body><a href='x'>Supplemental Material</a></body></html>")
        zf.writestr(
            "supp_gr.252981.119_Supplemental_Materials_.docx",
            _docx("Supplemental Figure S1 legend.\nSupplemental Methods, sequencing and alignment."),
        )
        for figure in _FIGURES:
            zf.writestr(figure, b"\xff\xd8\xff\xe0JFIF")
    return buffer.getvalue()


@pytest.fixture
def _groff_world(monkeypatch):
    """Every external boundary this paper touches, answered with captured public evidence."""
    from types import SimpleNamespace

    from app.services import validation_extraction_service as ext
    from app.services.literature.fulltext_service import FullTextResult
    from app.services.supplement_inventory import parse_jats_supplements

    async def _cfg(_sess, _org):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _Client:
        """The provider as it behaved on the day, plus the correction the evidence enables.

        The first binding call sees prose only and returns prose: the unparseable response the
        owner's run actually got. The reconciliation call is distinguishable because its payload
        carries the supplement inventory, and THAT is the call that can answer, because it can see
        that S3 holds 194 rows of which 88 clear the fold-change cutoff.
        """

        def __init__(self):
            self.binding_payloads: list[str] = []
            # What the bundle endpoint answers, one entry per request: bytes, or an exception. The
            # last entry repeats. change_7.3 section 12: each failure mode is a sequence here.
            self.bundle: list = [_bundle()]
            self.bundle_calls = 0

        async def submit(self, prompt, payload, model, api_key, attachments=None):
            if prompt.startswith("You are binding"):
                self.binding_payloads.append(payload)
                # The reconciliation call carries the inventory or the paper's kept statements, and
                # that is the call that can answer. The read-time binding sees prose only and gets
                # the unparseable response the owner's run actually got.
                if "The paper's supplements:" in payload or "statements about its samples" in payload:
                    return (
                        '```json\n{"bindings": [{"claim_index": 0, "bound_key": null, '
                        '"reason": "S3 holds 194 rows; the 88 is the fold-change subset", '
                        '"confidence": 0.9, "threshold_kind": "padj", "sample_subset": "XX vs XY", '
                        '"qc_stage": "post-QC"}]}\n```'
                    )
                return "the model wrote prose instead of JSON"
            if prompt.startswith("You are judging"):
                return _JUDGMENT
            return _EXTRACTION

    client = _Client()

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", _cfg)
    monkeypatch.setattr(ext, "get_client", lambda _p: client)
    monkeypatch.setattr("app.services.validation_driver_service.get_client", lambda _p: client)
    monkeypatch.setattr("app.services.validation_driver_service.llm_provider_config_service.get_active", _cfg)

    async def _fetch_text(url: str) -> str:
        if "ega-archive.org" in url:
            return _EGA_FILES if url.endswith("/files") else _EGA_DATASETS
        raise RuntimeError(f"404 {url}")

    async def _fetch_bytes(url: str) -> bytes:
        if "supplementaryFiles" in url:
            answer = client.bundle[min(client.bundle_calls, len(client.bundle) - 1)]
            client.bundle_calls += 1
            if isinstance(answer, Exception):
                raise answer
            return answer
        raise RuntimeError(f"404 {url}")

    async def _full_text(**_kw):
        import xml.etree.ElementTree as ET

        # The article's own text, so the passages kept at read time are the paper's.
        body = " ".join(ET.fromstring(_JATS).itertext())
        return FullTextResult(
            text=body,
            source="europepmc",
            external_id="PMC6771404",
            supplements=parse_jats_supplements(_JATS),
        )

    monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", _fetch_text)
    monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", _fetch_text)
    monkeypatch.setattr("app.services.validation_driver_service.FullTextFetchService.fetch", staticmethod(_full_text))
    monkeypatch.setattr("app.services.validation_driver_service._deposit_bytes_fetcher", _fetch_bytes)
    # change_7.2 section 4: the public assessment is its own module now, so the gate can run it too.
    # Its boundaries are the same ones, patched where they are used.
    monkeypatch.setattr("app.services.validation_assessment.deposit_bytes_fetcher", _fetch_bytes)
    monkeypatch.setattr("app.services.validation_assessment.get_client", lambda _p: client)
    monkeypatch.setattr("app.services.validation_assessment.llm_provider_config_service.get_active", _cfg)
    return client


async def _run(session, admin_user, route="pipeline", approval="driver"):
    """Groff the way the owner ran it: ``driver`` chooses the route at the button and lets the driver
    approve it; ``hand`` reads the paper and approves at the C1 gate. change_7.3 section 12: study 34
    took the default deposit route under driver approval, which no test here had exercised."""
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_doi="10.1101/gr.252981.119",
        intended_route=route if approval == "driver" else None,
    )
    await session.flush()
    if approval == "driver":
        for _ in range(4):  # read, then decide the route, then finish the assessment
            await ValidationDriverService.advance_active_studies(session)
            await session.refresh(study)
        return study
    await ValidationDriverService.read_and_plan(session, study, None, admin_user.organization_id, admin_user.id)
    study = await ValidationStudyService.approve_plan(
        session, study.id, admin_user.organization_id, admin_user.id, route=route
    )
    await session.refresh(study)
    return study


_PATHS = [("deposit", "driver"), ("deposit", "hand"), ("pipeline", "driver"), ("pipeline", "hand")]


class TestTheDepositIsFound:
    @pytest.mark.asyncio
    async def test_the_extracted_accession_is_discovered(self, session, admin_user, _groff_world):
        """`source_accession` is NULL. The EGA id exists only in the extracted plan, and discovery
        was handed the empty one, which is why every answer came back NO."""
        study = await _run(session, admin_user)
        caps = study.evidence_json["capabilities"]
        assert [d["accession"] for d in caps["deposits"]] == ["EGAS00001003667"]

    @pytest.mark.asyncio
    async def test_the_report_never_says_the_paper_named_no_accession(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        rendered = str(study.evidence_json)
        assert "names no deposited accession" not in rendered

    @pytest.mark.asyncio
    async def test_the_reads_exist_and_are_not_acquirable(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        caps = study.evidence_json["capabilities"]
        assert caps["raw_data"]["value"] == "yes"
        assert caps["deposits"][0]["access"] == "controlled"
        assert caps["deposits"][0]["supported"] == "no"


class TestTheSupplementsAreFoundAndRead:
    @pytest.mark.asyncio
    async def test_all_three_supplements_are_resolved_with_distinct_roles(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        roles = {s["label"]: s["role"] for s in study.evidence_json["supplements"] if s.get("resolved")}
        assert roles["Supplemental File S1"] == "sample_metadata"
        assert roles["Supplemental File S2"] == "code"
        assert roles["Supplemental File S3"] == "results_table"

    @pytest.mark.asyncio
    async def test_the_results_table_is_never_offered_as_the_matrix(self, session, admin_user, _groff_world):
        """194 rows of DESeq2 output cannot rerun DESeq2. Treating it as the input would produce a
        'reproduction' that reads its own answer back."""
        study = await _run(session, admin_user)
        roles = [s.get("role") for s in study.evidence_json["supplements"]]
        assert "expression_matrix" not in roles

    @pytest.mark.asyncio
    async def test_the_results_table_is_measured_at_the_cutoff_the_paper_claims(
        self, session, admin_user, _groff_world
    ):
        """The cutoff comes from the claim, not from a constant. A paper claiming 1.5-fold gets
        1.5 measured; this one claims 2."""
        study = await _run(session, admin_user)
        s3 = next(s for s in study.evidence_json["supplements"] if s["label"] == "Supplemental File S3")
        assert s3["row_count"] == 194
        assert s3["threshold_splits"]["abs_log2fc>2"] == 88

    @pytest.mark.asyncio
    async def test_the_sex_linked_count_comes_from_the_chromosome_column(self, session, admin_user, _groff_world):
        """Groff claims 146 sex-linked genes. No fold-change threshold can produce that number, and
        no constant in production code should: the chromosome column has to be read."""
        study = await _run(session, admin_user)
        s3 = next(s for s in study.evidence_json["supplements"] if s["label"] == "Supplemental File S3")
        chromosomes = s3["category_counts"]["chr"]
        assert chromosomes.get("chrX", 0) + chromosomes.get("chrY", 0) == 146


class TestTheStudyReachesAnOutcome:
    @pytest.mark.asyncio
    async def test_it_does_not_stop_at_plan_ready(self, session, admin_user, _groff_world):
        """The owner's run is still sitting there, indistinguishable from a study waiting for
        someone to click approve."""
        study = await _run(session, admin_user)
        assert study.state == "classified"

    @pytest.mark.asyncio
    async def test_the_outcome_is_access_restricted_not_missing_data(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        assert study.classification == "access_restricted"

    @pytest.mark.asyncio
    async def test_the_reason_names_the_real_obstacle(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        assert "controlled" in (study.failure_reason or "").lower()
        assert "GEO" not in (study.failure_reason or "")


class TestTheUnbindableClaimSurvives:
    @pytest.mark.asyncio
    async def test_a_claim_nothing_measures_reaches_the_plan(self, session, admin_user, _groff_world):
        """The digital-karyotype finding is central to the paper and was dropped outright, so the
        assessment looked complete when it had never read it."""
        from app.services.reproduction_plan_service import ReproductionPlanService

        study = await _run(session, admin_user)
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        assert any("digital karyotype" in (t.claim_text or "") for t in plan.comparison_targets)


class TestOneFileIsOneResource:
    """Study 32 listed S1, S2 and S3 twice each: the JATS media element and the prose reference
    resolve to the same bytes."""

    @pytest.mark.asyncio
    async def test_each_supplement_appears_once(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        filenames = [s["filename"] for s in study.evidence_json["supplements"] if s.get("filename")]
        assert len(filenames) == len(set(filenames))

    @pytest.mark.asyncio
    async def test_the_prose_identifier_is_the_label_a_reader_sees(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        labels = {s["label"] for s in study.evidence_json["supplements"]}
        assert "Supplemental File S3" in labels

    @pytest.mark.asyncio
    async def test_both_ways_of_naming_it_are_kept(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        s3 = next(s for s in study.evidence_json["supplements"] if s["label"] == "Supplemental File S3")
        assert len(s3["references"]) >= 1


class TestRetrievalReachesTheCodeRow:
    @pytest.mark.asyncio
    async def test_a_downloaded_code_supplement_is_not_reported_as_not_attempted(
        self, session, admin_user, _groff_world
    ):
        """The contradiction in study 32: exists yes, accessible not_attempted, for a file the same
        run had downloaded, read and classified as code."""
        study = await _run(session, admin_user)
        sources = study.evidence_json["capabilities"]["code_sources"]
        s2 = next(s for s in sources if s.get("identifier") == "Supplemental File S2")
        assert s2["accessible"] == "yes"

    @pytest.mark.asyncio
    async def test_extraction_is_reported_separately_from_retrieval(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        sources = study.evidence_json["capabilities"]["code_sources"]
        s2 = next(s for s in sources if s.get("identifier") == "Supplemental File S2")
        assert s2["code_extracted"] is True


class TestTheEvidenceReachesTheInterpretation:
    """change_7.1 section 6. Study 32 discovered everything and revised nothing: extraction ran
    before the inventory existed and the production binding call never passed it."""

    @pytest.mark.asyncio
    async def test_the_reconciliation_call_receives_the_inventory(self, session, admin_user, _groff_world):
        await _run(session, admin_user)
        with_inventory = [p for p in _groff_world.binding_payloads if "The paper's supplements:" in p]
        assert with_inventory, "no binding call was made with the inspected evidence"

    @pytest.mark.asyncio
    async def test_the_inventory_it_receives_carries_the_measured_counts(self, session, admin_user, _groff_world):
        await _run(session, admin_user)
        payload = next(p for p in _groff_world.binding_payloads if "The paper's supplements:" in p)
        assert "194" in payload
        assert "88" in payload

    @pytest.mark.asyncio
    async def test_its_decision_lands_on_the_persisted_target(self, session, admin_user, _groff_world):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        study = await _run(session, admin_user)
        rows = (await session.execute(select(ComparisonTarget))).scalars().all()
        assert any(t.threshold_kind == "padj" and t.sample_subset == "XX vs XY" for t in rows)
        assert study.evidence_json["reconciliation"]["revisions"]


class TestTheOutcomeDescribesTheRealObstacle:
    @pytest.mark.asyncio
    async def test_the_reason_is_not_a_claim_about_the_whole_paper(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        assert "for this paper" not in (study.failure_reason or "")

    @pytest.mark.asyncio
    async def test_processed_results_and_a_reproduction_input_are_separate(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        completion = study.evidence_json["completion"]
        # change_7.3 section 4 (flagged test change): both facts are tri-state now.
        assert completion["processed_results_available"] == "yes"
        # change_7.4 section 1.3 (flagged test change): the input fact reads the acquisition record.
        assert completion["input_acquired"] == "no"

    @pytest.mark.asyncio
    async def test_the_completed_checks_are_named(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        completed = " ".join(study.evidence_json["completion"]["checks_completed"])
        assert "Supplemental File S2" in completed
        assert "Supplemental File S3" in completed


# ---- change_7.3 section 12: the regression the owner's run needed ----------------------------------


async def _summary(session, admin_user, study):
    from app.services.validation_report_summary import report_summary_for

    return await report_summary_for(session, study, admin_user.organization_id)


async def _issues(session, admin_user, study, outcome=None):
    from app.services.validation_issue_service import ValidationIssueService

    issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
    return [i for i in issues if outcome is None or i["outcome"] == outcome]


class TestEveryWayTheOwnerCouldHaveRunIt:
    """Study 34 took the default deposit route under driver approval; this file only ever ran the
    pipeline route with a bundle that always arrived."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route,approval", _PATHS)
    async def test_it_reaches_access_restricted(self, session, admin_user, _groff_world, route, approval):
        study = await _run(session, admin_user, route, approval)
        assert study.state == "classified"
        assert study.classification == "access_restricted"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route,approval", _PATHS)
    async def test_the_refusal_is_the_missing_adapter(self, session, admin_user, _groff_world, route, approval):
        study = await _run(session, admin_user, route, approval)
        assert study.evidence_json["route_decision"]["action"] == "no_adapter"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route,approval", _PATHS)
    async def test_the_headline_says_reproduction_was_not_attempted(
        self, session, admin_user, _groff_world, route, approval
    ):
        study = await _run(session, admin_user, route, approval)
        summary = await _summary(session, admin_user, study)
        assert summary["headline"]["key"] == "reproduction_not_attempted"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route,approval", _PATHS)
    async def test_both_legs_are_reported(self, session, admin_user, _groff_world, route, approval):
        study = await _run(session, admin_user, route, approval)
        completion = study.evidence_json["completion"]
        legs = {limitation["operation"] for limitation in completion["limitations"] + completion["other_legs"]}
        assert {"deposit", "pipeline"} <= legs


class TestTheBundleFailureModes:
    """Each way the attachment bundle can fail. In every one the attachments keep their identity and
    existence, processed results are not established, the failure is one issue and one notice, and
    the headline says reproduction was not attempted."""

    _FAILURES = {
        "404 on every attempt": lambda: [_NotFound()],
        "an unreadable zip": lambda: [b"this is not a zip"],
    }

    async def _failed(self, session, admin_user, world, failure, monkeypatch=None):
        world.bundle = self._FAILURES[failure]()
        return await _run(session, admin_user, "deposit", "driver")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["404 on every attempt", "an unreadable zip"])
    async def test_the_attachments_keep_their_identity_and_existence(self, session, admin_user, _groff_world, failure):
        study = await self._failed(session, admin_user, _groff_world, failure)
        summary = await _summary(session, admin_user, study)
        attachments = [a for a in summary["artifacts"] if a["kind"] == "attachment"]
        assert len(attachments) == 4
        assert all(a["identity"] for a in attachments)
        assert {a["retrieval"]["status"] for a in attachments} == {"failed"}
        assert study.evidence_json["capabilities"]["code_sources"][0]["exists"] == "yes"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["404 on every attempt", "an unreadable zip"])
    async def test_processed_results_are_not_established(self, session, admin_user, _groff_world, failure):
        study = await self._failed(session, admin_user, _groff_world, failure)
        assert study.evidence_json["completion"]["processed_results_available"] == "not_established"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["404 on every attempt", "an unreadable zip"])
    async def test_it_is_one_issue_and_one_notice(self, session, admin_user, _groff_world, failure):
        study = await self._failed(session, admin_user, _groff_world, failure)
        summary = await _summary(session, admin_user, study)
        assert len(await _issues(session, admin_user, study, "retrieval_failed")) == 1
        assert len(summary["retrieval_failures"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["404 on every attempt", "an unreadable zip"])
    async def test_the_summary_states_the_owner_s_four_facts(self, session, admin_user, _groff_world, failure):
        study = await self._failed(session, admin_user, _groff_world, failure)
        facts = (await _summary(session, admin_user, study))["facts"]
        assert facts["reproduction_attempted"] is False
        assert (facts["raw_data"]["deposited"], facts["raw_data"]["available_to_bioaf"]) == ("yes", "no")
        assert facts["raw_data"]["access"] == ["controlled"]
        assert (facts["attachments"]["identified"], facts["attachments"]["failed"]) == (4, 4)
        assert facts["inputs_acquired"] is False
        assert facts["claims_tested"] == 0

    @pytest.mark.asyncio
    async def test_an_oversized_bundle_is_the_same_kind_of_failure(
        self, session, admin_user, _groff_world, monkeypatch
    ):
        from app.services import supplement_inventory

        monkeypatch.setattr(supplement_inventory, "_MAX_BUNDLE_BYTES", 1024)
        study = await _run(session, admin_user, "deposit", "driver")
        summary = await _summary(session, admin_user, study)
        assert study.evidence_json["retrieval_ledger"][-1]["outcome"] == "too_large"
        assert study.evidence_json["completion"]["processed_results_available"] == "not_established"
        assert len(summary["retrieval_failures"]) == 1
        assert summary["headline"]["key"] == "reproduction_not_attempted"

    @pytest.mark.asyncio
    async def test_a_404_then_the_bundle_keeps_both_attempts_and_no_stale_failure(
        self, session, admin_user, _groff_world
    ):
        _groff_world.bundle = [_NotFound(), _bundle()]
        study = await _run(session, admin_user, "deposit", "driver")
        ledger = study.evidence_json["retrieval_ledger"]
        assert [e["outcome"] for e in ledger] == ["not_found", "retrieved"]
        summary = await _summary(session, admin_user, study)
        assert summary["retrieval_failures"] == []
        assert await _issues(session, admin_user, study, "retrieval_failed") == []
        assert study.evidence_json["completion"]["processed_results_available"] == "yes"
        assert summary["headline"]["key"] == "reproduction_not_attempted"


class TestTheGroffSpecificsAreRepresented:
    """The 54/51 population, the 194/88 cutoffs and the samples-described contradiction."""

    @pytest.mark.asyncio
    async def test_the_194_and_the_88_are_two_claims_with_their_own_cutoffs(self, session, admin_user, _groff_world):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        await _run(session, admin_user, "deposit", "driver")
        rows = (await session.execute(select(ComparisonTarget))).scalars().all()
        by_value = {t.claimed_value: t for t in rows}
        assert [c["kind"] for c in by_value[194.0].cutoffs] == ["padj"]
        assert {c["kind"] for c in by_value[88.0].cutoffs} == {"padj", "abs_log2fc"}
        assert by_value[194.0].contrast_index == by_value[88.0].contrast_index == 0

    @pytest.mark.asyncio
    async def test_the_contrast_takes_the_194_claim_s_cutoff_not_the_subset_s(self, session, admin_user, _groff_world):
        from app.services.reproduction_plan_service import ReproductionPlanService

        study = await _run(session, admin_user, "deposit", "driver")
        plan = await ReproductionPlanService.get_plan(session, study.id, admin_user.organization_id)
        assert plan.differential_design_json["contrasts"][0]["thresholds"] == {"padj": 0.05, "log2fc": None}

    @pytest.mark.asyncio
    async def test_the_54_is_marked_as_the_collected_inventory_not_rewritten(self, session, admin_user, _groff_world):
        from sqlalchemy import select

        from app.models.comparison_target import ComparisonTarget

        _groff_world.bundle = [_NotFound()]
        await _run(session, admin_user, "deposit", "driver")
        target = next(t for t in (await session.execute(select(ComparisonTarget))).scalars() if t.claimed_value == 54)
        assert target.claimed_value == 54
        assert "EGAS00001003667" in (target.unresolved_reason or "")
        assert "excluded" in target.unresolved_reason

    @pytest.mark.asyncio
    async def test_the_samples_described_contradiction_is_reported_unresolved(self, session, admin_user, _groff_world):
        _groff_world.bundle = [_NotFound()]
        study = await _run(session, admin_user, "deposit", "driver")
        unresolved = study.evidence_json["assessment"]["unresolved_contradictions"]
        assert unresolved, "the paraphrased blocker and the ok check were never compared"
        assert "Supplemental File S1 may carry them" in unresolved[0]["outcome"]
        assert "not retrieved in this attempt" in unresolved[0]["outcome"]

    @pytest.mark.asyncio
    async def test_the_inspected_sample_table_settles_it_when_the_bundle_arrives(
        self, session, admin_user, _groff_world
    ):
        study = await _run(session, admin_user, "deposit", "driver")
        contradictions = study.evidence_json["assessment"]["contradictions"]
        assert contradictions and contradictions[0]["status"] == "resolved"
        assert "54" in contradictions[0]["settled_by"]

    @pytest.mark.asyncio
    async def test_the_read_keeps_the_paper_s_exclusion_statement(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user, "deposit", "driver")
        statements = study.evidence_json["paper_passages"]["statements"]
        assert any("three TE biopsy samples were excluded" in s for s in statements)
