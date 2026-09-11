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

_EXTRACTION = (
    '```json\n{"accessions": ["EGAS00001003667"], "sample_structure": {"organism": "Homo sapiens"}, '
    '"method": {"assay": "bulk RNA-seq"}, '
    '"claims": [{"metric_key": "", "claim_text": "digital karyotypes were concordant with PGT-A", '
    '"source_locator": "Results"}, '
    '{"metric_key": "differentially_expressed_genes", "value": 88, "unit": "genes", '
    '"claim_text": "88 of the 194 significant genes had |log2FC| > 2", '
    '"threshold": 2.0, "threshold_kind": "abs_log2fc", "source_locator": "Results"}], '
    '"data_availability": "restricted", '
    '"code_availability": [{"kind": "supplementary", "identifier": "Supplemental File S2"}], '
    '"blockers": []}\n```'
)


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

        async def submit(self, prompt, payload, model, api_key, attachments=None):
            if prompt.startswith("You are binding"):
                self.binding_payloads.append(payload)
                if "The paper's supplements:" in payload:
                    return (
                        '```json\n{"bindings": [{"claim_index": 0, "bound_key": null, '
                        '"reason": "S3 holds 194 rows; the 88 is the fold-change subset", '
                        '"confidence": 0.9, "threshold_kind": "padj", "sample_subset": "XX vs XY", '
                        '"qc_stage": "post-QC"}]}\n```'
                    )
                return "the model wrote prose instead of JSON"
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
            return _bundle()
        raise RuntimeError(f"404 {url}")

    async def _full_text(**_kw):
        return FullTextResult(
            text="the paper body",
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


async def _run(session, admin_user):
    study = await ValidationStudyService.create_study(
        session,
        admin_user.organization_id,
        admin_user.id,
        source_doi="10.1101/gr.252981.119",
        intended_route="pipeline",
    )
    await session.flush()
    for _ in range(4):  # read, then decide the route, then finish the assessment
        await ValidationDriverService.advance_active_studies(session)
        await session.refresh(study)
    return study


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
        assert completion["reproduction_input_available"] == "no"

    @pytest.mark.asyncio
    async def test_the_completed_checks_are_named(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        completed = " ".join(study.evidence_json["completion"]["checks_completed"])
        assert "Supplemental File S2" in completed
        assert "Supplemental File S3" in completed
