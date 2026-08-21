"""Which values a scientist states at launch belong on the sample afterwards.

Design section 1: a value written at launch persists only where the column
already maps to a ``Sample`` field. ``patient`` is a fact about the material;
mag's ``group`` is a co-assembly design and belongs to the run.

Design section 9 qualifies that, and the qualifier is the load-bearing half. A
scientist filling a column a pipeline CONSTRAINS is not stating what the sample
is, they are choosing something that pipeline will accept. Writing that back
lets the narrowest vocabulary in the catalog overwrite real biology: a sample
recorded ``47,XXY`` becomes ``NA`` because sarek only speaks XX/XY/NA. So a
constrained column never writes back, and a field that already holds a value is
never overwritten.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _open_text_contract():
    """A pipeline that asks for sex and does not dictate its vocabulary.

    sarek constrains the column, so a value entered for sarek is an
    accommodation. A pipeline that leaves it open is the case where the
    scientist really is stating the sample's sex.
    """
    return parse_contract(
        {
            "items": {
                "type": "object",
                "properties": {
                    "sample": {"type": "string"},
                    "sex": {"type": "string"},
                    "patient": {"type": "string"},
                },
                "required": ["sample"],
            }
        }
    )


def _make_sample(sample_id: int, external_id: str, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = []
    return sample


def _updates(contract, samples, sample_values):
    return SampleSheetService.sample_field_updates(contract, samples, sample_values)


class TestAStatedFactReachesTheSample:
    def test_a_stated_patient_becomes_the_donor(self):
        sample = _make_sample(1, "SAMPLE-1")

        assert _updates(_contract("sarek"), [sample], {"1": {"patient": "P-77"}}) == {1: {"donor_source": "P-77"}}

    def test_an_unconstrained_sex_is_the_scientist_stating_the_sample(self):
        sample = _make_sample(1, "SAMPLE-1")

        updates = _updates(_open_text_contract(), [sample], {"1": {"sex": "46,XX/46,XY"}})

        assert updates == {1: {"sex": "46,XX/46,XY"}}

    def test_a_value_bioaf_sourced_itself_is_not_a_statement(self):
        """Only what the scientist typed writes back. Everything else came FROM
        the sample, so writing it back would be a round trip that can only lose."""
        sample = _make_sample(1, "SAMPLE-1", donor_source="P-1")

        assert _updates(_contract("sarek"), [sample], {}) == {}


class TestAnAccommodationStaysOnTheRun:
    def test_a_constrained_column_never_writes_back(self):
        """sarek speaks XX, XY and NA. Whatever a scientist picks there is a
        value sarek will accept, not a claim about the sample."""
        sample = _make_sample(1, "SAMPLE-1")

        assert _updates(_contract("sarek"), [sample], {"1": {"sex": "XX"}}) == {}

    def test_a_field_that_already_has_a_value_is_not_overwritten(self):
        """A sample recorded 47,XXY keeps it. The run gets what it needs; the
        record keeps what is true."""
        sample = _make_sample(1, "SAMPLE-1", sex="47,XXY")

        assert _updates(_open_text_contract(), [sample], {"1": {"sex": "XX"}}) == {}

    def test_a_design_column_never_writes_back(self):
        """mag's group controls co-assembly. It describes this run's design, not
        the sample, and it is deliberately absent from the field map."""
        sample = _make_sample(1, "SAMPLE-1")

        assert _updates(_contract("mag"), [sample], {"1": {"group": "gut"}}) == {}

    def test_the_identity_column_never_renames_the_sample(self):
        """The name column maps to external_id so bioAF can FILL it. Writing it
        back would let a samplesheet rename the sample it came from, and every
        output already produced under the old name would stop matching it."""
        sample = _make_sample(1, "SAMPLE-1")

        assert _updates(_contract("sarek"), [sample], {"1": {"sample": "SAMPLE_1"}}) == {}

    def test_a_blank_statement_writes_nothing(self):
        sample = _make_sample(1, "SAMPLE-1")

        assert _updates(_contract("sarek"), [sample], {"1": {"patient": "   "}}) == {}

    def test_values_apply_by_sample_id_not_by_position(self):
        """The rule the whole entry surface rests on, asserted here too: a value
        keyed to a sample that is not in this launch reaches nothing."""
        first, second = _make_sample(1, "SAMPLE-1"), _make_sample(2, "SAMPLE-2")

        updates = _updates(_contract("sarek"), [first, second], {"2": {"patient": "P-2"}})

        assert updates == {2: {"donor_source": "P-2"}}
