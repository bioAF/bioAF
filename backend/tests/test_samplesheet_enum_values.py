"""The values a schema actually accepts, including the ones bioAF could not see.

``parse_contract`` read a column's ``enum`` and kept only the string entries, so
two real vocabularies were invisible:

- **Integer enums.** raredisease's ``phenotype`` accepts ``0``, ``1`` or ``2``
  and sarek's ``status`` accepts ``0`` or ``1``. Filtering to strings left both
  as an empty list, which every caller reads as "anything goes". A scientist
  typing ``affected`` got it emitted, and nf-schema rejected the sheet after the
  launch.

- **Enums declared through ``oneOf``.** raredisease's ``sex`` is a PED code:
  integer ``0/1/2`` or the string ``other``, expressed as two branches. Nothing
  read the branches, so bioAF believed the column unconstrained and would emit a
  ``Sample.sex`` of ``XX`` straight into a sheet raredisease cannot accept.

Both are the same failure the schema-driven work exists to remove: a sheet that
passes every check bioAF makes and dies minutes later inside Nextflow.

A CSV cell is text, so the values are held in the form a row will carry them:
``0`` becomes ``"0"``. And an empty enum still means "anything goes", never
"nothing is allowed", so a ``oneOf`` where any branch is unconstrained yields no
constraint at all.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import SamplesMissingRequiredFieldsError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_file(filename: str):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.tags_json = []
    return f


def _reads(name: str) -> list:
    return [_make_file(f"{name}_R1_001.fastq.gz"), _make_file(f"{name}_R2_001.fastq.gz")]


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _check(contract, samples, sample_values=None):
    return SampleSheetService.check_contract_satisfiable(contract, samples, {}, sample_values=sample_values)


def _generate(contract, samples, sample_values=None):
    return SampleSheetService.generate_from_contract(contract, samples, {}, sample_values=sample_values)


# -- Numeric vocabularies are read, in the form a CSV row carries them --


def test_an_integer_enum_is_read_as_the_text_a_row_would_hold():
    """raredisease's phenotype is 0, 1 or 2. A samplesheet cell is text, so the
    contract holds the values a row will actually carry and the comparison in
    _cell has something to match."""
    assert _contract("raredisease").enum_for("phenotype") == ["0", "1", "2"]


def test_sareks_status_enum_is_read_too():
    """The other integer enum in the captured set. It carries a default, so it
    never blocked, which is exactly why the gap went unnoticed."""
    assert _contract("sarek").enum_for("status") == ["0", "1"]


def test_a_string_enum_is_unchanged():
    """The control. rnastructurome's condition was always visible and must stay
    exactly as it was."""
    assert _contract("rnastructurome").enum_for("condition") == ["treated", "untreated", "denatured"]


# -- Vocabularies split across oneOf branches --


def test_an_enum_declared_through_oneof_is_read_from_every_branch():
    """raredisease's sex is `integer 0/1/2` OR `string "other"`. Read as one
    vocabulary in the order the schema declares it."""
    assert _contract("raredisease").enum_for("sex") == ["0", "1", "2", "other"]


def test_a_branch_without_an_enum_leaves_the_column_unconstrained():
    """rnasplice's fastq_2 is `a .fastq.gz path` OR `an empty string`, and
    neither branch names values. An empty enum means anything goes, so a
    partially-constrained column must yield nothing rather than a fence built
    from the half that happened to be listed."""
    assert _contract("rnasplice").enum_for("fastq_2") == []


# -- The vocabularies now do their job --


def test_a_value_outside_an_integer_enum_is_dropped_and_blocks():
    """The failure this closes. `affected` reads as a reasonable answer for a
    phenotype, is not one of 0, 1 or 2, and used to be emitted unchallenged."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]
    values = {"1": {"sex": "1", "case_id": "FAM01", "phenotype": "affected"}}

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("raredisease"), samples, sample_values=values)

    assert "phenotype" in exc.value.details["missing_columns"]


def test_a_value_inside_an_integer_enum_is_kept():
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]
    values = {"1": {"sex": "1", "case_id": "FAM01", "phenotype": "2"}}

    _check(_contract("raredisease"), samples, sample_values=values)

    assert "2" in _generate(_contract("raredisease"), samples, sample_values=values)


def test_a_recorded_sex_the_pipeline_cannot_express_blocks_rather_than_shipping():
    """Design section 9, end to end. A sample recorded as 47,XXY is real
    biology; raredisease's sex column is a PED code that cannot express it. The
    value is dropped, so the run blocks and asks, instead of emitting a sheet
    raredisease rejects. What it must never do is change Sample.sex."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"), sex="47,XXY")]

    with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
        _check(_contract("raredisease"), samples, sample_values={"1": {"case_id": "FAM01", "phenotype": "2"}})

    assert "sex" in exc.value.details["missing_columns"]
    assert samples[0].sex == "47,XXY"


def test_the_per_run_accommodation_launches_without_touching_the_sample():
    """The other half. The scientist supplies something raredisease accepts for
    this run, the launch proceeds, and the sample's own record is untouched."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"), sex="47,XXY")]
    values = {"1": {"case_id": "FAM01", "phenotype": "2", "sex": "other"}}

    _check(_contract("raredisease"), samples, sample_values=values)

    assert "other" in _generate(_contract("raredisease"), samples, sample_values=values)
    assert samples[0].sex == "47,XXY"


# -- Seeing a vocabulary does not mean imposing it on the sample --


def test_a_newly_visible_enum_still_does_not_constrain_a_sample_field():
    """The risk this change carries. raredisease's sex now HAS a visible enum,
    and the rule that a pipeline's vocabulary never fences the sample's own
    field has to survive that. Otherwise reading the schema better would be the
    thing that finally writes a PED code into the LIMS as though it were sex."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]

    specs = {s["name"]: s for s in SampleSheetService.per_sample_inputs(_contract("raredisease"), samples, {})}

    assert specs["sex"]["allowed_values"] == ["0", "1", "2", "other"]
    assert specs["sex"]["constrained"] is False
    assert specs["sex"]["sample_field"] == "sex"


def test_a_pipeline_parameter_with_a_numeric_enum_is_offered_as_a_closed_list():
    """phenotype is not recorded on the sample, so the schema's values are the
    whole truth and the grid may offer exactly them."""
    samples = [_make_sample(1, "PROBAND_A", files=_reads("PROBAND_A"))]

    specs = {s["name"]: s for s in SampleSheetService.per_sample_inputs(_contract("raredisease"), samples, {})}

    assert specs["phenotype"]["constrained"] is True
    assert specs["phenotype"]["allowed_values"] == ["0", "1", "2"]
