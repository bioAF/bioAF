"""Refusing a launch that cannot produce a valid samplesheet.

Today a pipeline bioAF cannot build a sheet for still launches: a node scales up,
containers pull, and the run dies inside Nextflow on a schema error the user did
not write. These decide the same thing before anything is provisioned.

Two outcomes, because they have different remedies:
  - a required column has no source, which the user can fix by filling the field
    or attaching the file the pipeline asked for
  - the pipeline wants no per-sample file at all, so no amount of sample metadata
    or attached files will help and the launch is refused outright

funcscan moved from the second to the first: it wants an assembly rather than
reads, and bioAF holds arbitrary files per sample, so a sample carrying one can
launch it. See test_sample_sheet_file_columns.py.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import (
    PipelineNotSampleLaunchableError,
    SamplesMissingRequiredFieldsError,
)
from app.services.samplesheet_schema import parse_contract
from app.services.sample_sheet_service import SampleSheetService

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_sample(sample_id: int, external_id: str, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = []
    return sample


def _check(contract, samples, parameters=None):
    return SampleSheetService.check_contract_satisfiable(contract, samples, parameters or {})


# -- Class B: the pipeline wants a file this sample does not carry --


def test_non_read_pipeline_blocks_on_the_file_it_wants():
    """nf-core/funcscan takes assemblies. A sample with no assembly cannot feed
    it, so the launch stops before compute is provisioned. It is a BLOCK rather
    than a refusal because it is actionable: attach one and the run proceeds."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), [_make_sample(1, "S1")])

    assert "fasta" in exc.value.details["missing_columns"]


def test_the_block_says_what_the_pipeline_actually_wants():
    """A stop the user cannot act on is barely better than the crash it replaces,
    so the message names the input, not just the failure."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("funcscan"), [_make_sample(1, "S1")])

    assert "fasta" in str(exc.value)


def test_a_pipeline_wanting_no_per_sample_file_is_still_refused():
    """The refusal survives for the case it was always right for: nothing the
    user attaches can make this pipeline sample-launchable."""
    contract = parse_contract(
        {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["taxid"],
                "properties": {"sample": {"type": "string", "pattern": r"^\S+$"}, "taxid": {"type": "integer"}},
            },
        }
    )

    with pytest.raises(PipelineNotSampleLaunchableError) as exc:
        _check(contract, [_make_sample(1, "S1")])

    assert exc.value.details["required_inputs"] == ["taxid"]


def test_a_read_consuming_pipeline_is_not_refused_for_this_reason():
    """mag still blocks, but on its missing `group`, never as 'not launchable'.
    The two refusals have different remedies, so they must not be conflated."""
    _check(_contract("demo"), [_make_sample(1, "S1")])

    with pytest.raises(SamplesMissingRequiredFieldsError):
        _check(_contract("mag"), [_make_sample(1, "S1", treatment_condition="x")])


# -- Class A2 and C: a required column with no source --


def test_missing_required_field_blocks_and_names_the_column():
    """sarek requires patient, sourced from donor_source. A sample without one
    cannot produce a valid sheet."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("sarek"), [_make_sample(1, "S1", donor_source=None)])

    assert "patient" in exc.value.details["missing_columns"]


def test_the_block_names_the_sample_field_the_user_must_fill():
    """'patient is missing' is not actionable. 'Donor source is empty on these
    samples' is."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("sarek"), [_make_sample(1, "S1")])

    assert exc.value.details["missing_columns"]["patient"]["sample_field"] == "donor_source"


def test_the_block_lists_only_the_samples_actually_missing_the_value():
    """Listing every sample would send the user hunting through the ones that
    are already fine."""
    samples = [
        _make_sample(1, "HAS_IT", donor_source="D1"),
        _make_sample(2, "MISSING_A"),
        _make_sample(3, "MISSING_B"),
    ]
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("sarek"), samples)

    offenders = exc.value.details["missing_columns"]["patient"]["samples"]
    assert [s["external_id"] for s in offenders] == ["MISSING_A", "MISSING_B"]
    assert [s["id"] for s in offenders] == [2, 3]


def test_satisfied_required_fields_do_not_block():
    _check(_contract("sarek"), [_make_sample(1, "S1", donor_source="DONOR_7")])


# -- Class A2: design columns block rather than being guessed --


def test_mag_blocks_on_group_rather_than_guessing_it():
    """group controls co-assembly. bioAF has treatment_condition and must not
    use it: the user has to say what the assembly design is."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("mag"), [_make_sample(1, "S1", treatment_condition="drug_treated")])

    assert "group" in exc.value.details["missing_columns"]
    assert exc.value.details["missing_columns"]["group"]["sample_field"] is None


# -- Defaults and unknown schemas --


def test_a_required_column_with_a_schema_default_does_not_block():
    """nf-schema fills it, so bioAF leaving it empty is fine."""
    contract = parse_contract(
        {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["sample", "fastq_1", "replicate"],
                "properties": {
                    "sample": {"type": "string"},
                    "fastq_1": {"type": "string", "pattern": r"^\S+\.fastq\.gz$"},
                    "replicate": {"type": "integer", "default": 1},
                },
            },
        }
    )
    _check(contract, [_make_sample(1, "S1")])


def test_an_empty_contract_never_blocks():
    """No schema means 'we do not know', which must fall back to today's
    behavior rather than refuse a launch that works now."""
    _check(parse_contract(None), [_make_sample(1, "S1")])
    _check(parse_contract({"garbage": True}), [_make_sample(1, "S1")])


def test_enum_column_that_cannot_be_satisfied_blocks_with_its_legal_values():
    """A required enum column bioAF cannot fill is as blocking as any other, and
    the legal values are what the user needs to choose from."""
    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("taxprofiler"), [_make_sample(1, "S1")])

    detail = exc.value.details["missing_columns"]["instrument_platform"]
    assert "ILLUMINA" in detail["allowed_values"]
