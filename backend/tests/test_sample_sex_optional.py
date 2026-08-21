"""Sample.sex is optional, and stays optional.

nf-core/raredisease requires a per-sample `sex`, and bioAF had nowhere to put it.
It now lives on the sample beside organism, tissue_type and donor_source, so it
is entered once and reused by every run rather than retyped per launch.

The constraint these lock down is that MOST assays neither use nor need it. It
must never become required on intake, and a sample without it must remain fully
usable for every pipeline that does not ask for it.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import SamplesMissingRequiredFieldsError
from app.models.experiment import Experiment
from app.models.organization import Organization
from app.models.sample import Sample
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"

# raredisease's shape: sex is required by the pipeline, optional on the sample.
_REQUIRES_SEX = parse_contract(
    {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["sample", "fastq_1", "sex"],
            "properties": {
                "sample": {"type": "string"},
                "fastq_1": {"pattern": r"^\S+\.fastq\.gz$"},
                "sex": {"type": "string"},
            },
        },
    }
)


def _sample(sex=None):
    s = MagicMock()
    s.id = 1
    s.external_id = "S1"
    s.sex = sex
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition"):
        setattr(s, attr, None)
    s._input_files = []
    return s


class TestSexIsOptionalOnTheSample:
    @pytest.mark.asyncio
    async def test_a_sample_can_be_created_without_sex(self, session):
        """The field must never be required on intake."""
        org = Organization(name="SexOptionalOrg", setup_complete=True)
        session.add(org)
        await session.flush()
        exp = Experiment(name="E", organization_id=org.id, status="registered")
        session.add(exp)
        await session.flush()

        s = Sample(experiment_id=exp.id, external_id="NO_SEX", organism="Homo sapiens")
        session.add(s)
        await session.flush()

        assert s.id is not None
        assert s.sex is None

    @pytest.mark.asyncio
    async def test_sex_round_trips_when_supplied(self, session):
        org = Organization(name="SexSuppliedOrg", setup_complete=True)
        session.add(org)
        await session.flush()
        exp = Experiment(name="E", organization_id=org.id, status="registered")
        session.add(exp)
        await session.flush()

        s = Sample(experiment_id=exp.id, external_id="HAS_SEX", sex="female")
        session.add(s)
        await session.flush()

        assert s.sex == "female"


class TestSexOnlyMattersToPipelinesThatAskForIt:
    def test_a_pipeline_that_does_not_need_sex_ignores_it_entirely(self):
        """The common case: a sample with no sex runs anything not asking for it."""
        contract = parse_contract(json.loads((FIXTURES / "demo.json").read_text()))

        SampleSheetService.check_contract_satisfiable(contract, [_sample(sex=None)], {})
        csv_text = SampleSheetService.generate_from_contract(contract, [_sample(sex=None)], {})

        assert "sex" not in csv_text.splitlines()[0]

    def test_a_pipeline_requiring_sex_blocks_when_it_is_empty(self):
        with pytest.raises(SamplesMissingRequiredFieldsError) as exc:
            SampleSheetService.check_contract_satisfiable(_REQUIRES_SEX, [_sample(sex=None)], {})

        assert exc.value.details["missing_columns"]["sex"]["sample_field"] == "sex"

    def test_a_pipeline_requiring_sex_launches_once_it_is_filled(self):
        SampleSheetService.check_contract_satisfiable(_REQUIRES_SEX, [_sample(sex="female")], {})

        csv_text = SampleSheetService.generate_from_contract(_REQUIRES_SEX, [_sample(sex="female")], {})
        header = csv_text.splitlines()[0].split(",")
        assert csv_text.splitlines()[1].split(",")[header.index("sex")] == "female"

    def test_sex_is_not_asked_for_at_launch(self):
        """It belongs on the sample, not in a per-run form, so the user does not
        retype it every launch."""
        assert "sex" not in [s["name"] for s in SampleSheetService.required_user_inputs(_REQUIRES_SEX)]
