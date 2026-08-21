"""Which samplesheet columns must be collected from the user at launch.

A required column bioAF cannot source blocks the launch. For a column whose value
is constant across the whole run, one dropdown fixes that; the schema even
enumerates the legal values. This is what the launch dialog renders.

The discipline is the same one that governs auto-filling: only columns on an
explicit run-level allowlist are offered as a single value. A column that varies
per sample must NOT appear here, because one value applied to every row would
silently mislabel samples rather than fail.
"""

import json
from pathlib import Path

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _names(specs):
    return [s["name"] for s in specs]


def _by_name(specs, name):
    return next(s for s in specs if s["name"] == name)


# -- What gets offered --


def test_taxprofiler_asks_for_instrument_platform():
    specs = SampleSheetService.required_user_inputs(_contract("taxprofiler"))

    assert "instrument_platform" in _names(specs)


def test_the_offered_field_carries_the_schemas_own_allowed_values():
    """The dropdown's options come from the pipeline, so they cannot drift."""
    spec = _by_name(SampleSheetService.required_user_inputs(_contract("taxprofiler")), "instrument_platform")

    assert "ILLUMINA" in spec["allowed_values"]
    assert "OXFORD_NANOPORE" in spec["allowed_values"]
    assert len(spec["allowed_values"]) == 11


def test_rnasplice_asks_for_strandedness_without_bioafs_illegal_auto():
    """rnasplice's enum is forward/reverse/unstranded. Offering bioAF's 'auto'
    default would put an invalid value in front of the user."""
    spec = _by_name(SampleSheetService.required_user_inputs(_contract("rnasplice")), "strandedness")

    assert set(spec["allowed_values"]) == {"forward", "reverse", "unstranded"}
    assert "auto" not in spec["allowed_values"]


def test_a_free_text_run_level_column_is_offered_with_no_options():
    """fastquorum's read_structure is a UMI layout string, constant per library
    prep. No enum, so the field is free text rather than a dropdown."""
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "read_structure"],
            "properties": {
                "sample": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
                "read_structure": {"type": "string"},
            },
        },
    }
    spec = _by_name(SampleSheetService.required_user_inputs(parse_contract(schema)), "read_structure")

    assert spec["allowed_values"] == []
    assert spec["required"] is True


# -- What is deliberately NOT offered --


def test_a_column_bioaf_can_already_source_is_not_asked_for():
    """sarek's patient comes from donor_source. Asking would be noise."""
    specs = SampleSheetService.required_user_inputs(_contract("sarek"))

    assert "patient" not in _names(specs)
    assert "sample" not in _names(specs)


def test_read_columns_are_never_asked_for():
    specs = SampleSheetService.required_user_inputs(_contract("demo"))

    assert _names(specs) == []


def test_a_per_sample_column_is_not_offered_as_one_run_level_value():
    """riboseq's `type` is riboseq/rnaseq/tiseq and a run PAIRS ribosome-profiling
    samples with matched RNA-seq samples. One value for every row would destroy
    the pairing while still running, so it must block instead of being asked
    for as a single choice."""
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "type"],
            "properties": {
                "sample": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
                "type": {"enum": ["riboseq", "rnaseq", "tiseq"]},
            },
        },
    }
    specs = SampleSheetService.required_user_inputs(parse_contract(schema))

    assert "type" not in _names(specs)


def test_a_design_column_is_not_offered_as_one_run_level_value():
    """mag's group controls co-assembly and differs per sample. Collecting one
    value would assign every sample to the same assembly group."""
    specs = SampleSheetService.required_user_inputs(_contract("mag"))

    assert "group" not in _names(specs)


def test_a_column_with_a_schema_default_is_not_asked_for():
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "strandedness"],
            "properties": {
                "sample": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
                "strandedness": {"enum": ["forward", "reverse"], "default": "forward"},
            },
        },
    }
    assert SampleSheetService.required_user_inputs(parse_contract(schema)) == []


def test_a_non_launchable_pipeline_asks_for_nothing():
    """funcscan cannot run from samples at all, so collecting values would
    imply a launch that is still impossible."""
    assert SampleSheetService.required_user_inputs(_contract("funcscan")) == []


def test_an_empty_contract_asks_for_nothing():
    assert SampleSheetService.required_user_inputs(parse_contract(None)) == []


# -- Supplying the value actually unblocks the launch --


def test_supplying_the_value_satisfies_the_check():
    contract = _contract("taxprofiler")
    specs = SampleSheetService.required_user_inputs(contract)

    class S:
        id = 1
        external_id = "S1"
        donor_source = "D1"
        organism = "Homo sapiens"
        tissue_type = None
        treatment_condition = None
        sex = None
        _input_files = []

    params = {s["name"]: s["allowed_values"][0] if s["allowed_values"] else "x" for s in specs}
    SampleSheetService.check_contract_satisfiable(contract, [S()], params)
