"""change_7.5 section 1.7: the repository's sample records reach column association.

Column association has read `evidence["sample_manifest"]` since plan_7 step 7, and nothing wrote it, so
GEO's sample titles and GSMs reached it only in a unit test. The manifest parser skipped
`!Sample_geo_accession`, which is the identifier a paper's design names. Inspection gathered design
samples from every contrast, not the selected one.
"""

import pytest

from app.models.validation_study import ValidationStudy
from app.services.deposit_metadata_association import associate_columns, rewrite_design_to_columns
from app.services.literature.accession_manifest_service import parse_series_matrix
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService

_MATRIX_TEXT = "\n".join(
    [
        '!Sample_title\t"ctrl rep one"\t"treated rep one"',
        '!Sample_geo_accession\t"GSM9001"\t"GSM9002"',
        '!Sample_characteristics_ch1\t"treatment: none"\t"treatment: drug"',
    ]
)


def test_the_manifest_parser_reads_each_samples_gsm():
    samples, _ = parse_series_matrix(_MATRIX_TEXT)
    assert [s["geo_accession"] for s in samples] == ["GSM9001", "GSM9002"]


def test_a_column_named_by_its_gsm_is_placed_by_the_repository():
    samples, _ = parse_series_matrix(_MATRIX_TEXT)
    [row] = associate_columns(["GSM9002"], manifest=samples)
    assert row["source"] == "series_matrix"
    assert row["sample_accession"] == "GSM9002"
    assert row["condition"] == "treatment: drug"


def test_a_title_equal_to_a_column_name_is_authoritative_even_with_no_condition():
    manifest = [{"title": "ctrl rep one", "geo_accession": "GSM9001", "condition": ""}]
    [row] = associate_columns(["ctrl rep one"], manifest=manifest)
    assert row["source"] == "series_matrix"
    assert row["sample_accession"] == "GSM9001"
    assert row["confidence"] == 1.0


def test_a_design_that_names_gsms_resolves_to_title_named_columns():
    samples, _ = parse_series_matrix(_MATRIX_TEXT)
    associations = associate_columns(["ctrl rep one", "treated rep one"], manifest=samples)
    design = {
        "contrasts": [
            {
                "name": "drug vs none",
                "test_samples": ["GSM9002"],
                "reference_samples": ["GSM9001"],
                "subjects": {"GSM9001": "d1", "GSM9002": "d1"},
            }
        ]
    }
    rewritten, status, reason = rewrite_design_to_columns(design, associations, contrast_index=0)
    assert status == "ok", reason
    contrast = rewritten["contrasts"][0]
    assert contrast["test_samples"] == ["treated rep one"]
    assert contrast["reference_samples"] == ["ctrl rep one"]
    # Placed by accession, so a declared pairing carries onto the columns.
    assert contrast["subjects"] == {"treated rep one": "d1", "ctrl rep one": "d1"}


# ---- the manifest is written where the deposit is listed ----


@pytest.mark.asyncio
async def test_listing_the_deposit_writes_the_repository_sample_records(session, admin_user):
    listing = '<html><a href="GSE9000_counts.tsv.gz">GSE9000_counts.tsv.gz</a></html>'

    async def _fetch(url):
        if url.endswith("/suppl/"):
            return listing
        if "series_matrix" in url:
            return _MATRIX_TEXT
        raise RuntimeError(f"404 {url}")

    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE9000",
        state="acquiring_processed",
        evidence_json={"route": "deposit"},
    )
    session.add(study)
    await session.flush()
    await ReproductionPlanService.create_plan(session, study, admin_user.id, pipeline_key="nf-core/rnaseq")
    await session.flush()

    evidence = dict(study.evidence_json)
    await ValidationDriverService._choose_from_deposit(session, study, evidence, fetcher=_fetch)

    manifest = evidence.get("sample_manifest") or []
    assert [s["geo_accession"] for s in manifest] == ["GSM9001", "GSM9002"]


# ---- inspection reads the selected contrast's samples only ----


class _Storage:
    def __init__(self, files):
        self.files = files

    async def read_text(self, uri, *, encoding="utf-8"):
        return self.files[uri]


@pytest.mark.asyncio
async def test_inspection_measures_the_selected_contrasts_samples_only(session, admin_user):
    matrix = "\tA_1\tA_2\tB_1\tB_2\nG1\t1\t2\t3\t4\nG2\t5\t6\t7\t8\n"
    design = {
        "selected_contrast": {"contrast_index": 1, "decided_by": "human"},
        "contrasts": [
            {"name": "day 7", "assay": "bulk RNA-seq", "test_samples": ["D7_KO"], "reference_samples": ["D7_WT"]},
            {
                "name": "A vs B",
                "assay": "bulk RNA-seq",
                "test_samples": ["A_1", "A_2"],
                "reference_samples": ["B_1", "B_2"],
            },
        ],
    }
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"value_type": "counts"},
            "deposit": {
                "files": [
                    {"file_id": 1, "filename": "m.tsv", "storage_uri": "s3://m", "artifact_type": "deposited_matrix"}
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    await session.flush()

    await ValidationDriverService._handle_inspecting_deposit(
        session, study, storage_adapter=_Storage({"s3://m": matrix})
    )

    inspection = study.evidence_json["deposit_inspection"]
    assert inspection["design_samples_missing"] == []
    assert inspection["design_samples_found"] == 4
