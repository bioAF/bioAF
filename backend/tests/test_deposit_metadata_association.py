"""plan_7 step 7: associate sample metadata with the matrix's columns.

The bioinformaticians' longest requirement, and the hardest:

    The metadata should be available for download. It will need to be associated manually by the LLM.
    Sometimes it will need to infer from the sample / file name. Other times it will need to read the
    GEO description and content. Other times the metadata has it, but the headers may be incorrect.

Three sources, and they are not equally good, so every association carries the source it came from.
A condition the DEPOSITOR stated in the series matrix and a condition a model INFERRED from a
filename are both usable and must never render as the same strength of evidence.

The column names asserted here are real: `Control-KD_1..3` / `H2AS40-KD_1..3` from GSE274331's TPM
table, and the `act`/`con` filename convention from GSE157174, which the model spotted unprompted
when step 2 was run against that deposit on the demo.
"""

import pytest

from app.services.deposit_metadata_association import (
    associate_columns,
    infer_from_column_names,
    parse_metadata_table,
    rewrite_design_to_columns,
)

_GSE274331_COLUMNS = [
    "Control-KD_1",
    "Control-KD_2",
    "Control-KD_3",
    "H2AS40-KD_1",
    "H2AS40-KD_2",
    "H2AS40-KD_3",
]


# ---- source 3: inference from the column names alone ----


def test_infers_arms_from_a_real_column_naming_convention():
    """GSE274331's columns carry the condition and the replicate. No metadata file is needed and
    asking for one would be theatre."""
    out = infer_from_column_names(_GSE274331_COLUMNS)
    assert {r["column"]: r["condition"] for r in out} == {
        "Control-KD_1": "Control-KD",
        "Control-KD_2": "Control-KD",
        "Control-KD_3": "Control-KD",
        "H2AS40-KD_1": "H2AS40-KD",
        "H2AS40-KD_2": "H2AS40-KD",
        "H2AS40-KD_3": "H2AS40-KD",
    }
    assert all(r["source"] == "column_name" for r in out)


def test_inference_keeps_the_replicate_number():
    out = {r["column"]: r["replicate"] for r in infer_from_column_names(_GSE274331_COLUMNS)}
    assert out["Control-KD_2"] == "2"
    assert out["H2AS40-KD_3"] == "3"


def test_inference_is_marked_lower_confidence_than_a_stated_source():
    """It is a guess from a string. A usable one, but the gate has to be able to show it as weaker
    than the depositor's own words."""
    assert all(r["confidence"] < 1.0 for r in infer_from_column_names(_GSE274331_COLUMNS))


def test_columns_with_no_shared_convention_infer_nothing():
    """Two arms need at least two groups. Inventing a grouping from unrelated names would produce a
    contrast the paper never ran."""
    assert infer_from_column_names(["s1", "s2", "s3"]) == []


def test_inference_needs_at_least_two_groups():
    assert infer_from_column_names(["WT_1", "WT_2", "WT_3"]) == []


# ---- source 1: the downloaded metadata file, whose headers may be wrong ----

_META_ODD_HEADERS = "Sample\tGroup\tRep\nControl-KD_1\tcontrol\t1\nH2AS40-KD_1\tknockdown\t1\n"


def test_parses_a_metadata_table_through_a_column_map():
    """ "The headers may be incorrect" is the stated case, so the metadata file goes through the same
    model-decides / person-picks seam `column_resolution` already provides for result tables."""
    rows = parse_metadata_table(
        _META_ODD_HEADERS, column_map={"sample_id": "Sample", "condition": "Group", "replicate": "Rep"}
    )
    assert rows == [
        {"sample_id": "Control-KD_1", "condition": "control", "replicate": "1", "batch": None},
        {"sample_id": "H2AS40-KD_1", "condition": "knockdown", "replicate": "1", "batch": None},
    ]


def test_a_metadata_table_with_recognisable_headers_needs_no_map():
    rows = parse_metadata_table("sample\tcondition\nA\tctrl\nB\ttreat\n")
    assert rows[0]["sample_id"] == "A"
    assert rows[0]["condition"] == "ctrl"


def test_a_column_map_naming_a_missing_column_is_ignored_not_honoured():
    """Same guard `_mapped` applies in result_set_normalizer: a wrong hint must never blank a table
    the alias list would have parsed on its own."""
    rows = parse_metadata_table("sample\tcondition\nA\tctrl\n", column_map={"condition": "NoSuchColumn"})
    assert rows[0]["condition"] == "ctrl"


def test_an_unparseable_metadata_table_yields_nothing_rather_than_raising():
    assert parse_metadata_table("") == []
    assert parse_metadata_table("just one column\nvalue\n") == []


# ---- the association, over all three sources ----


def test_the_metadata_file_wins_over_inference():
    """The depositor wrote it down. A guess from a column name cannot outrank that."""
    rows = associate_columns(
        _GSE274331_COLUMNS,
        metadata_rows=[{"sample_id": "Control-KD_1", "condition": "vehicle", "replicate": "1", "batch": None}],
    )
    first = next(r for r in rows if r["column"] == "Control-KD_1")
    assert first["condition"] == "vehicle"
    assert first["source"] == "metadata_file"


def test_the_series_matrix_is_used_when_there_is_no_metadata_file():
    """GEO's own !Sample_title / !Sample_characteristics_ch1, which accession_manifest_service
    already fetches and currently spends only on rendering the picker."""
    manifest = [
        {"experiment_accession": "SRX1", "title": "Control-KD_1", "condition": "genotype: wild type"},
        {"experiment_accession": "SRX2", "title": "H2AS40-KD_1", "condition": "genotype: H2AS40 KD"},
    ]
    rows = associate_columns(["Control-KD_1", "H2AS40-KD_1"], manifest=manifest)
    by_col = {r["column"]: r for r in rows}
    assert by_col["Control-KD_1"]["condition"] == "genotype: wild type"
    assert by_col["Control-KD_1"]["source"] == "series_matrix"
    assert by_col["Control-KD_1"]["sample_accession"] == "SRX1"


def test_inference_is_the_last_resort():
    rows = associate_columns(_GSE274331_COLUMNS)
    assert all(r["source"] == "column_name" for r in rows)


def test_every_row_carries_where_it_came_from():
    """The rule that makes the gate honest: a stated condition and an inferred one are both usable
    and must never render as the same strength of evidence."""
    rows = associate_columns(
        _GSE274331_COLUMNS,
        metadata_rows=[{"sample_id": "Control-KD_1", "condition": "vehicle", "replicate": None, "batch": None}],
    )
    assert {r["source"] for r in rows} == {"metadata_file", "column_name"}
    assert all(r.get("source") for r in rows)


def test_a_row_for_a_column_the_matrix_lacks_is_dropped():
    """The same guard `parse_column_resolution` applies to an invented column: the model cannot
    invent a sample."""
    rows = associate_columns(
        ["Control-KD_1"],
        metadata_rows=[
            {"sample_id": "Control-KD_1", "condition": "vehicle", "replicate": None, "batch": None},
            {"sample_id": "GHOST_SAMPLE", "condition": "treated", "replicate": None, "batch": None},
        ],
    )
    assert [r["column"] for r in rows] == ["Control-KD_1"]


def test_a_column_with_no_metadata_anywhere_is_still_returned_unresolved():
    """Silence about a column would read as "there is no such column". The gate needs to show it as
    present and unexplained so a person can fill it in."""
    rows = associate_columns(["weird_column_1", "weird_column_2"])
    assert len(rows) == 2
    assert all(r["condition"] is None for r in rows)
    assert all(r["source"] == "unresolved" for r in rows)


# ---- rewriting the design onto the matrix's columns ----

_DESIGN = {
    "selected_contrast": {"contrast_index": 0, "decided_by": "only_contrast"},
    "contrasts": [
        {
            "name": "KD vs control",
            "test_condition": "H2AS40-KD",
            "reference_condition": "Control-KD",
            "test_samples": ["GSM8447570", "GSM8447571"],
            "reference_samples": ["GSM8447568", "GSM8447569"],
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}


def test_the_design_is_rewritten_onto_matrix_columns():
    """Mirrors what `_resolve_sample_design` does for Sample.external_id on the pipeline route: the
    DE run must match the matrix's columns by construction."""
    associations = [
        {"column": "Control-KD_1", "condition": "Control-KD", "sample_accession": "GSM8447568", "source": "x"},
        {"column": "Control-KD_2", "condition": "Control-KD", "sample_accession": "GSM8447569", "source": "x"},
        {"column": "H2AS40-KD_1", "condition": "H2AS40-KD", "sample_accession": "GSM8447570", "source": "x"},
        {"column": "H2AS40-KD_2", "condition": "H2AS40-KD", "sample_accession": "GSM8447571", "source": "x"},
    ]
    out, status, reason = rewrite_design_to_columns(_DESIGN, associations, contrast_index=0)
    assert status == "ok"
    assert reason is None
    c = out["contrasts"][0]
    assert c["test_samples"] == ["H2AS40-KD_1", "H2AS40-KD_2"]
    assert c["reference_samples"] == ["Control-KD_1", "Control-KD_2"]
    assert out["thresholds"] == {"log2fc": 1.0, "padj": 0.05}


def test_the_design_is_matched_on_condition_when_accessions_are_absent():
    """A deposited matrix rarely names GSMs in its columns. Matching on the CONDITION is what makes
    a column-named matrix usable at all."""
    associations = [
        {"column": "Control-KD_1", "condition": "Control-KD", "sample_accession": None, "source": "column_name"},
        {"column": "H2AS40-KD_1", "condition": "H2AS40-KD", "sample_accession": None, "source": "column_name"},
    ]
    out, status, _ = rewrite_design_to_columns(_DESIGN, associations, contrast_index=0)
    assert status == "ok"
    assert out["contrasts"][0]["test_samples"] == ["H2AS40-KD_1"]
    assert out["contrasts"][0]["reference_samples"] == ["Control-KD_1"]


def test_a_design_that_maps_no_columns_holds_rather_than_running_a_partial_contrast():
    """The same held-before-compute contract `_resolve_sample_design` has. An arm with no samples is
    not a smaller experiment, it is not an experiment."""
    associations = [
        {"column": "totally_other_1", "condition": "something else", "sample_accession": None, "source": "column_name"}
    ]
    out, status, reason = rewrite_design_to_columns(_DESIGN, associations, contrast_index=0)
    assert status == "mismatch"
    assert reason and "arm" in reason.lower()


def test_an_empty_arm_is_a_mismatch_even_when_the_other_arm_resolves():
    associations = [
        {"column": "Control-KD_1", "condition": "Control-KD", "sample_accession": None, "source": "column_name"},
    ]
    _, status, reason = rewrite_design_to_columns(_DESIGN, associations, contrast_index=0)
    assert status == "mismatch"
    assert "H2AS40-KD" in reason


def test_a_design_with_no_contrasts_is_left_alone():
    out, status, _ = rewrite_design_to_columns({}, [], contrast_index=0)
    assert status == "ok"
    assert out == {}


@pytest.mark.parametrize("case", ["Control-KD", "control-kd", "CONTROL-KD"])
def test_condition_matching_ignores_case(case):
    associations = [
        {"column": "c1", "condition": case, "sample_accession": None, "source": "column_name"},
        {"column": "t1", "condition": "H2AS40-KD", "sample_accession": None, "source": "column_name"},
    ]
    out, status, _ = rewrite_design_to_columns(_DESIGN, associations, contrast_index=0)
    assert status == "ok"
    assert out["contrasts"][0]["reference_samples"] == ["c1"]


# ---- wired into the driver's inspection step ----

import pytest_asyncio  # noqa: E402

from app.models.validation_study import ValidationStudy  # noqa: E402
from app.services.reproduction_plan_service import ReproductionPlanService  # noqa: E402
from app.services.validation_driver_service import ValidationDriverService  # noqa: E402

_MATRIX = (
    "\tControl-KD_1\tControl-KD_2\tH2AS40-KD_1\tH2AS40-KD_2\n"
    "ENSG00000000003\t120\t118\t95\t97\n"
    "ENSG00000000005\t50\t52\t80\t84\n"
)


class _FakeStorage:
    def __init__(self, files):
        self.files = files

    async def read_text(self, uri, *, encoding="utf-8"):
        return self.files[uri]


@pytest_asyncio.fixture
async def study_with_design(session, admin_user):
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE274331",
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"primary_matrix": "m.tsv", "matrix_files": ["m.tsv"], "value_type": "counts"},
            "deposit": {
                "files": [
                    {
                        "file_id": 1,
                        "filename": "m.tsv",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    await ReproductionPlanService.create_plan(
        session,
        study,
        admin_user.id,
        pipeline_key="nf-core/rnaseq",
        differential_design=_DESIGN,
    )
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_the_design_is_rewritten_onto_the_matrix_during_inspection(session, study_with_design, admin_user):
    """The design names GSMs the matrix does not have. Matching on condition rewrites the arms to
    the matrix's own columns, so the differential test matches its input by construction."""
    await ValidationDriverService._handle_inspecting_deposit(
        session, study_with_design, storage_adapter=_FakeStorage({"s3://x/m.tsv": _MATRIX})
    )
    plan = await ReproductionPlanService.get_plan(session, study_with_design.id, admin_user.organization_id)
    c = plan.differential_design_json["contrasts"][0]
    assert c["test_samples"] == ["H2AS40-KD_1", "H2AS40-KD_2"]
    assert c["reference_samples"] == ["Control-KD_1", "Control-KD_2"]
    assert study_with_design.state == "reproducing"


@pytest.mark.asyncio
async def test_the_associations_and_their_sources_land_on_the_evidence(session, study_with_design):
    await ValidationDriverService._handle_inspecting_deposit(
        session, study_with_design, storage_adapter=_FakeStorage({"s3://x/m.tsv": _MATRIX})
    )
    rows = study_with_design.evidence_json["deposit_metadata_association"]
    assert len(rows) == 4
    assert {r["source"] for r in rows} == {"column_name"}
    assert all(r["reason"] for r in rows)


@pytest.mark.asyncio
async def test_a_matrix_whose_columns_match_no_arm_holds(session, admin_user):
    """The held-before-compute contract, on the deposit route."""
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE1",
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"primary_matrix": "m.tsv", "matrix_files": ["m.tsv"]},
            "deposit": {
                "files": [
                    {
                        "file_id": 1,
                        "filename": "m.tsv",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=_DESIGN
    )
    await session.flush()

    other = "\tfoo_1\tfoo_2\tbar_1\tbar_2\nG1\t1\t2\t3\t4\nG2\t5\t6\t7\t8\n"
    await ValidationDriverService._handle_inspecting_deposit(
        session, study, storage_adapter=_FakeStorage({"s3://x/m.tsv": other})
    )
    # change_7.4 sections 1.1 and 1.4: an empty arm is never retried; it concludes before compute.
    assert study.state == "classified"
    assert "arm" in study.evidence_json["deposit_failed"]["reason"].lower()


# ---- change_7.4 section 1.4: only the selected contrast, and a declared pairing survives ----

_TWO = {
    "contrasts": [
        # A second experiment's contrast, listed first. Its conditions are not in this matrix at all,
        # and it is not the selected one, so it must not hold the run.
        {"name": "day 7", "test_condition": "KO day 7", "reference_condition": "WT day 7"},
        _DESIGN["contrasts"][0],
    ],
    "thresholds": _DESIGN["thresholds"],
    "selected_contrast": {"contrast_index": 1},
}

_BY_ACCESSION = [
    {"column": "Control-KD_1", "condition": "Control-KD", "sample_accession": "GSM8447568", "source": "metadata_file"},
    {"column": "Control-KD_2", "condition": "Control-KD", "sample_accession": "GSM8447569", "source": "metadata_file"},
    {"column": "H2AS40-KD_1", "condition": "H2AS40-KD", "sample_accession": "GSM8447570", "source": "metadata_file"},
    {"column": "H2AS40-KD_2", "condition": "H2AS40-KD", "sample_accession": "GSM8447571", "source": "metadata_file"},
]


def test_only_the_selected_contrast_is_validated_and_rewritten():
    out, status, _ = rewrite_design_to_columns(_TWO, _BY_ACCESSION, contrast_index=1)
    assert status == "ok"
    assert out["contrasts"][1]["test_samples"] == ["H2AS40-KD_1", "H2AS40-KD_2"]
    # The other contrast stays in the plan untouched.
    assert out["contrasts"][0] == _TWO["contrasts"][0]


def test_a_declared_pairing_is_carried_onto_the_column_names():
    """The 2026-09-11 reproduction: the rewrite replaced the sample ids with column names and left
    `subjects` keyed by the old ids, so the parameter builder found no label and ran unpaired."""
    paired = {
        **_DESIGN,
        "contrasts": [
            {
                **_DESIGN["contrasts"][0],
                "subjects": {"GSM8447568": "d1", "GSM8447569": "d2", "GSM8447570": "d1", "GSM8447571": "d2"},
            }
        ],
    }
    out, status, _ = rewrite_design_to_columns(paired, _BY_ACCESSION, contrast_index=0)
    assert status == "ok"
    assert out["contrasts"][0]["subjects"] == {
        "Control-KD_1": "d1",
        "Control-KD_2": "d2",
        "H2AS40-KD_1": "d1",
        "H2AS40-KD_2": "d2",
    }


def test_a_pairing_that_cannot_be_carried_stops_rather_than_running_unpaired():
    """Columns matched by condition cannot say which donor each one is."""
    paired = {
        **_DESIGN,
        "contrasts": [
            {
                **_DESIGN["contrasts"][0],
                "subjects": {"GSM8447568": "d1", "GSM8447569": "d2", "GSM8447570": "d1", "GSM8447571": "d2"},
            }
        ],
    }
    by_condition = [dict(a, sample_accession=None) for a in _BY_ACCESSION]
    _, status, reason = rewrite_design_to_columns(paired, by_condition, contrast_index=0)
    assert status == "pairing_lost"
    assert "pair" in reason.lower()


@pytest.mark.asyncio
async def test_the_parameter_builder_receives_the_carried_pairing(session, admin_user):
    """Through the driver: metadata places each column by accession, the rewrite carries the donor
    labels, and the analysis parameters get `block_labels`."""
    from app.models.file import File
    from app.models.template_notebook import TemplateNotebook

    session.add(
        TemplateNotebook(
            organization_id=admin_user.organization_id,
            name="DESeq2 headless",
            category="differential_expression",
            notebook_path="notebooks/de_bulk_deseq2.ipynb",
            parameters_json={},
            is_builtin=True,
        )
    )
    matrix_file = File(
        organization_id=admin_user.organization_id,
        filename="m.tsv",
        storage_uri="s3://x/m.tsv",
        file_type="table",
        source_type="external_deposit",
        artifact_type="deposited_matrix",
        uploader_user_id=admin_user.id,
    )
    session.add(matrix_file)
    await session.flush()
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE274331",
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"primary_matrix": "m.tsv", "matrix_files": ["m.tsv"], "value_type": "counts"},
            "deposit": {
                "files": [
                    {
                        "file_id": matrix_file.id,
                        "filename": "m.tsv",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    },
                    {
                        "file_id": 0,
                        "filename": "meta.tsv",
                        "storage_uri": "s3://x/meta.tsv",
                        "artifact_type": "deposited_metadata",
                    },
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    design = {
        **_DESIGN,
        "contrasts": [
            {
                **_DESIGN["contrasts"][0],
                "subjects": {"GSM8447568": "d1", "GSM8447569": "d2", "GSM8447570": "d1", "GSM8447571": "d2"},
            }
        ],
        "selected_contrast": {"contrast_index": 0, "decided_by": "human"},
    }
    plan = await ReproductionPlanService.create_plan(
        session, study, admin_user.id, pipeline_key="nf-core/rnaseq", differential_design=design
    )
    plan.finding_claim_json = {
        "kind": "gene",
        "confirmed": True,
        "finding_set": {"kind": "gene", "entities": [{"id": "x"}]},
    }
    await session.flush()
    metadata = (
        "sample\tgeo_accession\tcondition\n"
        "Control-KD_1\tGSM8447568\tControl-KD\nControl-KD_2\tGSM8447569\tControl-KD\n"
        "H2AS40-KD_1\tGSM8447570\tH2AS40-KD\nH2AS40-KD_2\tGSM8447571\tH2AS40-KD\n"
    )

    await ValidationDriverService._handle_inspecting_deposit(
        session, study, storage_adapter=_FakeStorage({"s3://x/m.tsv": _MATRIX, "s3://x/meta.tsv": metadata})
    )

    assert study.state == "reproducing", study.evidence_json.get("deposit_failed")
    assert study.evidence_json["level3"]["parameters"]["block_labels"] == "d1,d2,d1,d2"
