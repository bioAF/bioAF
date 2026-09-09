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
    '"source_locator": "Results"}], '
    '"data_availability": "restricted", "code_availability": [], "blockers": []}\n```'
)


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
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            if prompt.startswith("You are binding"):
                # The failure the owner actually saw. It must stay visible, not become a verdict.
                return "the model wrote prose instead of JSON"
            return _EXTRACTION

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", _cfg)
    monkeypatch.setattr(ext, "get_client", lambda _p: _Client())

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
    async def test_the_results_table_counts_are_measured(self, session, admin_user, _groff_world):
        study = await _run(session, admin_user)
        s3 = next(s for s in study.evidence_json["supplements"] if s["label"] == "Supplemental File S3")
        assert s3["row_count"] == 194
        assert s3["threshold_splits"]["abs_log2fc>2"] == 88


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
