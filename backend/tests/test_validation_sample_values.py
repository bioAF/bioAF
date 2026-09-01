"""A ratified differential design is a per-sample answer, and the driver was throwing it away.

``sample_values`` is fully built on the manual launch path: the schema declares the column, the
grid collects it, ``check_contract_satisfiable`` blocks without it and the preview shows what will
be submitted. The lit_validation driver launches with no form at all and passed none of it, so a
study was refused for any design column bioAF could not derive from the sample itself, which is
every column that MEANS anything: cutandrun's ``group``, atacseq's ``replicate``, sarek's
``patient``.

The design is not a guess. It is the contrast the scientist ratified at the C1 gate, already
resolved to real fetched samples, and it says which arm each sample is in and which subject it came
from. That is exactly what those columns ask for.
"""

import pytest

from app.services.samplesheet_schema import parse_contract
from app.services.validation_sample_values import sample_values_from_design


class _Sample:
    def __init__(self, sample_id: int, external_id: str):
        self.id = sample_id
        self.external_id = external_id


def _samples(*external_ids):
    return [_Sample(i + 1, e) for i, e in enumerate(external_ids)]


# cutandrun-shaped: a group per arm, a replicate within it, paired reads.
_CUTANDRUN = parse_contract(
    {
        "items": {
            "type": "object",
            "properties": {
                "group": {"type": "string", "pattern": "^[a-zA-Z0-9_]+$"},
                "replicate": {"type": "integer"},
                "fastq_1": {"type": "string", "format": "file-path"},
                "fastq_2": {"type": "string", "format": "file-path"},
            },
            "required": ["group", "replicate", "fastq_1"],
        }
    }
)

_PAIRED_DESIGN = {
    "contrasts": [
        {
            "name": "stimulated vs resting",
            "test_condition": "stimulated",
            "reference_condition": "resting",
            "test_samples": ["S1", "S2", "S3"],
            "reference_samples": ["S4", "S5", "S6"],
            "subjects": {"S1": "donor A", "S4": "donor A", "S2": "donor B", "S5": "donor B"},
        }
    ],
    "thresholds": {"log2fc": 1.0, "padj": 0.05},
}


def test_each_sample_gets_the_arm_it_was_ratified_into():
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S2", "S3", "S4", "S5", "S6"), _CUTANDRUN)

    assert values["1"]["group"] == "stimulated"
    assert values["3"]["group"] == "stimulated"
    assert values["4"]["group"] == "resting"
    assert values["6"]["group"] == "resting"


def test_replicate_numbers_within_the_arm_not_across_the_sheet():
    """A replicate is the nth sample OF ITS GROUP. Numbering across the sheet would tell cutandrun
    that the reference arm has replicates 4, 5 and 6 of a group with three members."""
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S2", "S3", "S4", "S5", "S6"), _CUTANDRUN)

    assert [values[str(i)]["replicate"] for i in (1, 2, 3)] == ["1", "2", "3"]
    assert [values[str(i)]["replicate"] for i in (4, 5, 6)] == ["1", "2", "3"]


def test_a_design_with_no_condition_names_still_names_the_arms():
    design = {"contrasts": [{"name": "KO vs WT", "test_samples": ["S1"], "reference_samples": ["S2"], "subjects": {}}]}
    values = sample_values_from_design(design, _samples("S1", "S2"), _CUTANDRUN)

    assert values["1"]["group"] == "test"
    assert values["2"]["group"] == "reference"


def test_a_condition_the_column_cannot_spell_is_respelled_not_dropped():
    """Schemas constrain these columns: cutandrun's group takes no spaces. A condition named the way
    a paper words it would otherwise block the launch on a pattern the scientist never typed."""
    design = {
        "contrasts": [
            {
                "test_condition": "LPS-stimulated (4h)",
                "reference_condition": "untreated",
                "test_samples": ["S1"],
                "reference_samples": ["S2"],
                "subjects": {},
            }
        ]
    }
    values = sample_values_from_design(design, _samples("S1", "S2"), _CUTANDRUN)

    import re

    group = values["1"]["group"]
    assert re.fullmatch(r"^[a-zA-Z0-9_]+$", group), group
    assert group.lower().startswith("lps_stimulated"), group
    assert values["2"]["group"] == "untreated"


def test_the_subject_map_becomes_the_patient_column():
    contract = parse_contract(
        {
            "items": {
                "type": "object",
                "properties": {
                    "patient": {"type": "string", "pattern": "^[a-zA-Z0-9_]+$"},
                    "sample": {"type": "string"},
                    "fastq_1": {"type": "string", "format": "file-path"},
                },
                "required": ["patient", "sample", "fastq_1"],
            }
        }
    )
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S2", "S3", "S4", "S5", "S6"), contract)

    assert values["1"]["patient"] == "donor_A"
    assert values["4"]["patient"] == "donor_A"
    # S3 and S6 have no subject in the design; inventing one would pair samples nobody paired.
    assert "patient" not in values.get("3", {})


def test_only_columns_the_pipeline_declares_are_answered():
    """Emitting a column a pipeline never declared is how a whole sheet fails nf-schema validation."""
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S4"), _CUTANDRUN)

    assert set(values["1"]) == {"group", "replicate"}


def test_a_pipeline_with_no_contract_is_told_nothing():
    """An empty contract means the schema was absent or unrecognized, not that the pipeline wants
    these columns. Answering questions nobody asked is the guess this whole path avoids."""
    assert sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S4"), parse_contract(None)) == {}


def test_a_qc_only_study_yields_nothing():
    for design in ({}, None, {"contrasts": []}):
        assert sample_values_from_design(design, _samples("S1"), _CUTANDRUN) == {}


def test_a_sample_in_neither_arm_is_left_alone():
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1", "S9"), _CUTANDRUN)

    assert "1" in values
    assert "2" not in values


def test_a_value_no_spelling_can_satisfy_is_left_for_the_gap_to_report():
    """Nothing punctuation can do turns a condition name into a GCA accession. Emitting something
    that merely looks right would name the wrong thing; the launch blocks and says which column."""
    contract = parse_contract(
        {
            "items": {
                "type": "object",
                "properties": {
                    "group": {"type": "string", "pattern": "^GC[AF]_[0-9]{9}\\.[0-9]+$"},
                    "fastq_1": {"type": "string", "format": "file-path"},
                },
                "required": ["group", "fastq_1"],
            }
        }
    )
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1"), contract)

    assert values == {}


def test_an_enum_column_only_takes_a_value_the_schema_lists():
    """`condition` is rnasplice's differential contrast and rnastructurome's rf-norm chemistry. The
    schema's own enum is what tells the two apart, so a design's arm name never lands in one it does
    not list."""
    contract = parse_contract(
        {
            "items": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string", "enum": ["treated", "untreated", "denatured"]},
                    "fastq_1": {"type": "string", "format": "file-path"},
                },
                "required": ["condition", "fastq_1"],
            }
        }
    )
    values = sample_values_from_design(_PAIRED_DESIGN, _samples("S1"), contract)

    assert values == {}


# ---- the point of all of it: the launch stops being refused ----


class _File:
    def __init__(self, sample_external_id, read):
        self.storage_uri = f"gs://b/{sample_external_id}_{read}.fastq.gz"
        self.filename = f"{sample_external_id}_{read}.fastq.gz"
        self.file_type = "fastq"
        self.tags_json = [f"read:R{read}", "lane:001"]


class _SequencedSample(_Sample):
    def __init__(self, sample_id, external_id):
        super().__init__(sample_id, external_id)
        self._input_files = [_File(external_id, 1), _File(external_id, 2)]


def test_a_cutandrun_shaped_launch_stops_being_refused():
    """The whole point. Before this, `group` and `replicate` had no source bioAF could read, so
    every cutandrun study was refused at the preflight rather than submitted broken. The ratified
    design supplies both and the same check now passes."""
    from app.exceptions import SamplesMissingRequiredFieldsError
    from app.services.sample_sheet_service import SampleSheetService

    samples = [_SequencedSample(i + 1, e) for i, e in enumerate(["S1", "S2", "S3", "S4", "S5", "S6"])]

    with pytest.raises(SamplesMissingRequiredFieldsError) as ei:
        SampleSheetService.check_contract_satisfiable(_CUTANDRUN, samples, {}, {})
    assert "group" in str(ei.value) or "replicate" in str(ei.value)

    values = sample_values_from_design(_PAIRED_DESIGN, samples, _CUTANDRUN)
    SampleSheetService.check_contract_satisfiable(_CUTANDRUN, samples, {}, values)


def test_the_sheet_that_results_carries_the_arms_it_was_given():
    """A satisfiable check is not the same as a correct sheet. This is the sheet that would run."""
    from app.services.sample_sheet_service import SampleSheetService

    samples = [_SequencedSample(i + 1, e) for i, e in enumerate(["S1", "S2", "S3", "S4", "S5", "S6"])]
    values = sample_values_from_design(_PAIRED_DESIGN, samples, _CUTANDRUN)

    csv = SampleSheetService.generate_from_contract(_CUTANDRUN, samples, {}, values)
    lines = csv.strip().splitlines()
    header = lines[0].split(",")
    rows = [dict(zip(header, line.split(","))) for line in lines[1:]]

    assert [r["group"] for r in rows] == ["stimulated"] * 3 + ["resting"] * 3
    assert [r["replicate"] for r in rows] == ["1", "2", "3", "1", "2", "3"]
