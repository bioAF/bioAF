"""change_7.4 section 1.1: each place the deposit route holds says what failed.

Study 37 listed GSE144396, downloaded `GSE144396_RNA-Seq_NormalizedCounts.txt.gz`, read it and found
it usable. The design rewrite then found no column for an arm, `_hold_deposit` received the sentence,
the wording matched no signature, and the hold was treated as transient: three attempts in about two
minutes and a report reading "bioAF could not reach the deposit after 3 attempts". Every call site
here knows which kind of failure it has, so it passes that as a cause.

Each test drives one call site through the driver with fixture fetchers and asserts the cause that
reached the record, whether it was retried, and the limitation it concluded with.
"""

import gzip
import json
from types import SimpleNamespace

import httpx
import pytest

from app.models.validation_study import ValidationStudy
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService

_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE1/suppl/"
_MATRIX = b"gene\tWT_1\tWT_2\tKO_1\tKO_2\nTP53\t10\t12\t50\t55\nGAPDH\t100\t110\t105\t99\n"

_DIR_LISTING = """<html><body>
<a href="GSE1_counts.tsv.gz">GSE1_counts.tsv.gz</a>
<a href="GSE1_RAW.tar">GSE1_RAW.tar</a>
</body></html>"""

_EMPTY_LISTING = "<html><body></body></html>"

_UNFILTERED_LISTING = """<html><body>
<a href="GSM1_raw_feature_bc_matrix.h5">GSM1_raw_feature_bc_matrix.h5</a>
<a href="GSM2_raw_feature_bc_matrix.h5">GSM2_raw_feature_bc_matrix.h5</a>
</body></html>"""

_COVERAGE_LISTING = """<html><body>
<a href="GSM1_WT.bw">GSM1_WT.bw</a>
<a href="GSM2_KO.bw">GSM2_KO.bw</a>
<a href="GSE1_RAW.tar">GSE1_RAW.tar</a>
</body></html>"""

_UNPLACEABLE_LISTING = """<html><body>
<a href="GSE1_supplement_A.txt.gz">GSE1_supplement_A.txt.gz</a>
<a href="GSE1_supplement_B.txt.gz">GSE1_supplement_B.txt.gz</a>
</body></html>"""

_DESIGN = {
    "contrasts": [
        {
            "name": "KO vs WT",
            "assay": "RNA-seq",
            "test_condition": "KO",
            "reference_condition": "WT",
            "test_samples": ["GSM3", "GSM4"],
            "reference_samples": ["GSM1", "GSM2"],
            "thresholds": {"padj": 0.05, "log2fc": 1.0},
        }
    ],
    "thresholds": {"padj": 0.05, "log2fc": 1.0},
    "selected_contrast": {"contrast_index": 0, "decided_by": "only_contrast", "reason": "one contrast"},
}


def _status_error(code: int, url: str = _BASE + "GSE1_counts.tsv.gz"):
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(f"{code}", request=request, response=httpx.Response(code, request=request))


def _bytes_fetcher(pages: dict | None = None, *, raise_for=None):
    async def fetch(url: str) -> bytes:
        if raise_for is not None:
            raise raise_for
        if url not in (pages or {}):
            raise _status_error(404, url)
        return pages[url]

    return fetch


def _listing(listing: str | None = None, *, raise_for=None):
    async def fetch(url: str) -> str:
        if url.endswith("filelist.txt"):
            raise _status_error(404, url)
        if raise_for is not None:
            raise raise_for
        return listing

    return fetch


class _Storage:
    def __init__(self, files: dict | None = None, *, fail_reads: bool = False):
        self.files = dict(files or {})
        self.fail_reads = fail_reads

    async def write_text(self, uri, text, *, content_type="text/plain"):
        self.files[uri] = text

    async def read_text(self, uri, *, encoding="utf-8"):
        if self.fail_reads:
            raise ConnectionError("storage did not answer")
        return self.files[uri]


async def _study(session, admin_user, *, state="acquiring_processed", evidence=None, accession="GSE1"):
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession=accession,
        intended_route="deposit",
        state=state,
        evidence_json={"route": "deposit", **(evidence or {})},
    )
    session.add(study)
    await session.flush()
    return study


async def _plan(session, study, admin_user, *, design=None, accessions=None):
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        pipeline_key="nf-core/rnaseq",
        differential_design=design,
        accessions=accessions,
    )
    await session.flush()


def _chosen(**extra):
    return {
        "deposit_inventory": {"accession": "GSE1", "entries": [], "triplets": []},
        "deposit_selection": {
            "primary_matrix": "GSE1_counts.tsv.gz",
            "matrix_files": ["GSE1_counts.tsv.gz"],
            "metadata_file": None,
            "value_type": "counts",
            "decided_by": "human",
        },
        **extra,
    }


def _acquired(filename="m.tsv", uri="s3://x/m.tsv", **extra):
    return {
        "deposit_selection": {"primary_matrix": filename, "matrix_files": [filename], "value_type": "counts"},
        "deposit": {
            "files": [{"file_id": 1, "filename": filename, "storage_uri": uri, "artifact_type": "deposited_matrix"}]
        },
        **extra,
    }


def _failed(study):
    return study.evidence_json["deposit_failed"]


def _limitation_kinds(study):
    return [limitation["kind"] for limitation in (study.evidence_json.get("completion") or {}).get("limitations", [])]


async def _autonomous(session, org_id, monkeypatch, response):
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.services import validation_driver_service as drv

    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    org.lit_validation_autonomy = "autonomous"
    await session.flush()

    async def fake_get_for_feature(sess, org_id, feature):
        return SimpleNamespace(provider="anthropic", model="m", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return response

    monkeypatch.setattr(drv.llm_provider_config_service, "get_for_feature", fake_get_for_feature)
    monkeypatch.setattr(drv, "get_client", lambda p: _C())


# ---- selection: the reading, the archive, the listing and the choice -------------------------------


class TestSelection:
    @pytest.mark.asyncio
    async def test_a_reading_that_named_no_accession_is_unidentified(self, session, admin_user):
        """Nothing establishes that the paper deposited nothing; bioAF found nothing to point at."""
        study = await _study(session, admin_user, accession=None)
        await _plan(session, study, admin_user, accessions=[])

        await ValidationDriverService._handle_acquiring_processed(session, study, inventory_fetcher=_listing(""))

        assert _failed(study)["cause"] == "input_unidentified"
        assert study.state == "classified"
        assert "missing_input" not in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_an_archive_with_no_adapter_is_no_adapter(self, session, admin_user):
        study = await _study(session, admin_user, accession=None)
        await _plan(session, study, admin_user, accessions=["EGAS00001003667"])

        await ValidationDriverService._handle_acquiring_processed(session, study, inventory_fetcher=_listing(""))

        assert _failed(study)["cause"] == "no_adapter"
        assert "unsupported_acquisition" in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_a_listing_that_did_not_answer_is_retried(self, session, admin_user):
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(raise_for=httpx.ConnectError("connection reset"))
        )

        assert _failed(study)["cause"] == "retrieval_transient"
        assert study.state == "acquiring_processed"
        assert study.evidence_json["acquisition_retry_at"]

    @pytest.mark.asyncio
    async def test_a_listing_refused_to_bioaf_is_access_refused(self, session, admin_user):
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(raise_for=_status_error(403, _BASE))
        )

        assert _failed(study)["cause"] == "access_refused"
        assert study.state == "classified"
        assert "access_refused" in _limitation_kinds(study)
        assert "controlled_access" not in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_a_listing_not_found_is_retried_within_the_bound(self, session, admin_user):
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(raise_for=_status_error(404, _BASE))
        )

        assert _failed(study)["cause"] == "retrieval_not_found"
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_an_empty_listing_is_an_absence_within_that_listing(self, session, admin_user):
        """The one call site here that can establish an absence, and it states its scope."""
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(_EMPTY_LISTING)
        )

        assert _failed(study)["cause"] == "no_input"
        assert "GSE1" in _failed(study)["reason"]
        assert "missing_input" in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_a_listing_of_kinds_bioaf_cannot_use_is_unsupported_processing(self, session, admin_user):
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(_UNFILTERED_LISTING)
        )

        assert _failed(study)["cause"] == "unsupported_processing"
        assert "cell-calling" in _failed(study)["reason"]

    @pytest.mark.asyncio
    async def test_a_listing_of_coverage_tracks_names_what_is_there(self, session, admin_user):
        """The failure-modes table: a listing of only kinds bioAF cannot analyze is
        unsupported_processing, naming what is there."""
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(_COVERAGE_LISTING)
        )

        assert _failed(study)["cause"] == "unsupported_processing"
        assert "coverage" in _failed(study)["reason"]
        assert "missing_input" not in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_a_listing_the_classifier_could_not_place_is_unidentified(self, session, admin_user):
        study = await _study(session, admin_user)

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(_UNPLACEABLE_LISTING)
        )

        assert _failed(study)["cause"] == "input_unidentified"
        assert study.classification == "inconclusive"

    @pytest.mark.asyncio
    async def test_a_model_that_declines_every_file_is_unidentified(self, session, admin_user, monkeypatch):
        """A model's decline is a decision from filenames. It never establishes an absence."""
        study = await _study(session, admin_user)
        await _autonomous(
            session,
            admin_user.organization_id,
            monkeypatch,
            "```json\n"
            + json.dumps(
                {"primary_matrix": None, "declined": True, "reason": "only coverage tracks", "confidence": 0.8}
            )
            + "\n```",
        )

        await ValidationDriverService._handle_acquiring_processed(
            session, study, inventory_fetcher=_listing(_DIR_LISTING)
        )

        assert _failed(study)["cause"] == "input_unidentified"
        assert study.classification == "inconclusive"
        assert "missing_input" not in _limitation_kinds(study)

    @pytest.mark.asyncio
    async def test_a_recorded_blocker_carries_the_cause_it_arose_with(self, session, admin_user):
        study = await _study(
            session,
            admin_user,
            evidence={
                "deposit_unusable": "This study deposited 2 file(s), but they are raw matrices.",
                "deposit_unusable_cause": "unsupported_processing",
                "deposit_selection": {"declined": True, "matrix_files": []},
            },
        )

        await ValidationDriverService._handle_acquiring_processed(session, study)

        assert _failed(study)["cause"] == "unsupported_processing"


# ---- the download ----------------------------------------------------------------------------------


class TestDownload:
    @pytest.mark.asyncio
    async def test_a_404_is_retried_and_names_the_location(self, session, admin_user):
        study = await _study(session, admin_user, evidence=_chosen())

        await ValidationDriverService._handle_acquiring_processed(
            session, study, fetcher=_bytes_fetcher({}), storage_adapter=_Storage()
        )

        assert _failed(study)["cause"] == "retrieval_not_found"
        assert study.state == "acquiring_processed"
        assert study.evidence_json["acquisition_attempts"] == 1

    @pytest.mark.asyncio
    async def test_a_404_that_persists_ends_as_a_retrieval_failure_at_that_location(self, session, admin_user):
        from app.services.validation_acquisition_outcome import MAX_ATTEMPTS

        study = await _study(session, admin_user, evidence=_chosen())
        for _ in range(MAX_ATTEMPTS):
            evidence = dict(study.evidence_json)
            evidence.pop("acquisition_retry_at", None)
            study.evidence_json = evidence
            await ValidationDriverService._handle_acquiring_processed(
                session, study, fetcher=_bytes_fetcher({}), storage_adapter=_Storage()
            )

        assert study.state == "classified"
        limitation = next(
            x for x in study.evidence_json["completion"]["limitations"] if x["kind"] == "retrieval_failed"
        )
        assert f"not found at {_BASE}GSE1_counts.tsv.gz" in limitation["detail"]
        assert "could not reach" not in limitation["detail"]
        assert study.classification == "inconclusive"

    @pytest.mark.asyncio
    async def test_a_403_is_access_refused_and_not_retried(self, session, admin_user):
        study = await _study(session, admin_user, evidence=_chosen())

        await ValidationDriverService._handle_acquiring_processed(
            session, study, fetcher=_bytes_fetcher(raise_for=_status_error(403)), storage_adapter=_Storage()
        )

        assert _failed(study)["cause"] == "access_refused"
        assert study.state == "classified"
        assert "acquisition_retry_at" not in study.evidence_json
        assert study.classification != "access_restricted"

    @pytest.mark.asyncio
    async def test_a_5xx_is_transient(self, session, admin_user):
        study = await _study(session, admin_user, evidence=_chosen())

        await ValidationDriverService._handle_acquiring_processed(
            session, study, fetcher=_bytes_fetcher(raise_for=_status_error(503)), storage_adapter=_Storage()
        )

        assert _failed(study)["cause"] == "retrieval_transient"
        assert study.state == "acquiring_processed"

    @pytest.mark.asyncio
    async def test_a_file_that_cannot_be_decoded_is_unreadable_and_named(self, session, admin_user):
        study = await _study(session, admin_user, evidence=_chosen())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            study,
            fetcher=_bytes_fetcher({_BASE + "GSE1_counts.tsv.gz": gzip.compress(b"\xff\xfe\xfd not a table")}),
            storage_adapter=_Storage(),
        )

        assert _failed(study)["cause"] == "input_unreadable"
        assert "GSE1_counts.tsv.gz" in _failed(study)["reason"]
        assert study.state == "classified"

    @pytest.mark.asyncio
    async def test_a_file_over_the_limit_is_a_resource_limit(self, session, admin_user, monkeypatch):
        from app.services import deposit_acquisition

        def too_large(filename, raw):
            raise deposit_acquisition.DepositTooLargeError("the selected deposit files total 3.0 GB, over the limit")

        monkeypatch.setattr(deposit_acquisition, "decode_deposit", too_large)
        study = await _study(session, admin_user, evidence=_chosen())

        await ValidationDriverService._handle_acquiring_processed(
            session,
            study,
            fetcher=_bytes_fetcher({_BASE + "GSE1_counts.tsv.gz": _MATRIX}),
            storage_adapter=_Storage(),
        )

        assert _failed(study)["cause"] == "resource_limit"
        assert "resource_limit" in _limitation_kinds(study)


# ---- inspection ------------------------------------------------------------------------------------


class TestInspection:
    @pytest.mark.asyncio
    async def test_an_acquisition_record_with_no_matrix_is_acquired_again(self, session, admin_user):
        """Not a failure of the deposit at all: bioAF's own record is incomplete. It is recorded as
        an issue, the record is cleared, and acquisition runs again under the same attempt bound."""
        from app.services.validation_issue_service import ValidationIssueService

        study = await _study(session, admin_user, state="inspecting_deposit", evidence={"deposit": {"files": []}})

        await ValidationDriverService._handle_inspecting_deposit(session, study, storage_adapter=_Storage())

        assert study.state == "acquiring_processed"
        assert "deposit" not in study.evidence_json
        assert study.evidence_json["acquisition_attempts"] == 1
        issues = await ValidationIssueService.list_for_study(session, study.id, admin_user.organization_id)
        assert any(i["outcome"] == "internal" for i in issues)

    @pytest.mark.asyncio
    async def test_a_stored_copy_that_cannot_be_read_back_is_transient(self, session, admin_user):
        study = await _study(session, admin_user, state="inspecting_deposit", evidence=_acquired())

        await ValidationDriverService._handle_inspecting_deposit(
            session, study, storage_adapter=_Storage(fail_reads=True)
        )

        assert _failed(study)["cause"] == "retrieval_transient"
        assert study.state == "inspecting_deposit"
        assert study.evidence_json["acquisition_retry_at"]

    @pytest.mark.asyncio
    async def test_a_table_with_no_rows_is_unreadable(self, session, admin_user):
        study = await _study(session, admin_user, state="inspecting_deposit", evidence=_acquired())

        await ValidationDriverService._handle_inspecting_deposit(
            session, study, storage_adapter=_Storage({"s3://x/m.tsv": "gene\tA\tB\n"})
        )

        assert _failed(study)["cause"] == "input_unreadable"
        assert study.state == "classified"

    @pytest.mark.asyncio
    async def test_a_single_sample_matrix_is_a_kind_bioaf_cannot_analyze(self, session, admin_user):
        study = await _study(session, admin_user, state="inspecting_deposit", evidence=_acquired())

        await ValidationDriverService._handle_inspecting_deposit(
            session, study, storage_adapter=_Storage({"s3://x/m.tsv": "gene\tonly\nTP53\t4\nGAPDH\t9\n"})
        )

        assert _failed(study)["cause"] == "unsupported_processing"


# ---- the design rewrite ----------------------------------------------------------------------------


class TestTheDesignRewrite:
    @pytest.mark.asyncio
    async def test_columns_placed_by_their_names_alone_are_an_unresolved_mapping(self, session, admin_user):
        """Study 37's shape. Nothing is established about the columns but their names, so the
        mapping is unresolved rather than the design incompatible, and it is never retried."""
        study = await _study(session, admin_user, state="inspecting_deposit", evidence=_acquired())
        await _plan(session, study, admin_user, design=_DESIGN)

        other = "\tfoo_1\tfoo_2\tbar_1\tbar_2\nG1\t1\t2\t3\t4\nG2\t5\t6\t7\t8\n"
        await ValidationDriverService._handle_inspecting_deposit(
            session, study, storage_adapter=_Storage({"s3://x/m.tsv": other})
        )

        assert _failed(study)["cause"] == "sample_mapping_unresolved"
        assert study.state == "classified"
        assert "acquisition_retry_at" not in study.evidence_json
        assert "could not reach" not in study.failure_reason

    @pytest.mark.asyncio
    async def test_columns_the_metadata_states_that_hold_neither_arm_are_an_incompatible_design(
        self, session, admin_user
    ):
        metadata = "sample\tcondition\nfoo_1\tDMSO\nfoo_2\tDMSO\nbar_1\tdrug\nbar_2\tdrug\n"
        evidence = _acquired()
        evidence["deposit"]["files"].append(
            {
                "file_id": 2,
                "filename": "meta.tsv",
                "storage_uri": "s3://x/meta.tsv",
                "artifact_type": "deposited_metadata",
            }
        )
        study = await _study(session, admin_user, state="inspecting_deposit", evidence=evidence)
        await _plan(session, study, admin_user, design=_DESIGN)

        other = "\tfoo_1\tfoo_2\tbar_1\tbar_2\nG1\t1\t2\t3\t4\nG2\t5\t6\t7\t8\n"
        await ValidationDriverService._handle_inspecting_deposit(
            session, study, storage_adapter=_Storage({"s3://x/m.tsv": other, "s3://x/meta.tsv": metadata})
        )

        assert _failed(study)["cause"] == "design_incompatible"
        assert "design_incompatible" in _limitation_kinds(study)


class TestAResumedStudyReDerivesItsCause:
    @pytest.mark.asyncio
    async def test_a_legacy_blocker_is_cleared_so_the_choice_is_made_again(self, session, admin_user):
        """A record written before causes carries only text. Resuming clears it, so the next
        attempt re-derives the cause where the hold arises."""
        from app.services.validation_study_service import ValidationStudyService

        study = await _study(
            session,
            admin_user,
            state="classified",
            evidence={
                "deposit_unusable": "the model found nothing in this deposit worth reproducing from",
                "deposit_selection": {"declined": True, "matrix_files": [], "reason": "nothing usable"},
                "deposit_failed": {"reason": "x", "kind": "terminal", "action": "no_input"},
            },
        )
        study.classification = "missing_data"
        await session.flush()

        resumed = await ValidationStudyService.resume_study(
            session, study.id, admin_user.organization_id, admin_user.id
        )

        assert "deposit_unusable" not in resumed.evidence_json
        assert "deposit_failed" not in resumed.evidence_json
        assert "deposit_selection" not in resumed.evidence_json
