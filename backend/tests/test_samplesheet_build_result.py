"""One build of a samplesheet, read by every consumer.

The review step, the entry grid, the launch's refusal, the identity snapshot and
the sample-field write-back each used to ask the sheet service separately, so
the same request was resolved three or four times and nothing held them to one
answer. ``SampleSheetService.build`` resolves it once into a ``SheetBuildResult``
holding the columns, attributable rows, the exact CSV, the identity column, the
omissions and the blocking issues, and each consumer reads from that.

These pin that the result says what the established facades say, so a consumer
moving onto it changes nothing a scientist sees.
"""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.exceptions import PipelineNotSampleLaunchableError, SamplesMissingRequiredFieldsError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_inputs import SheetSampleInput
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _read(name: str, read_type: str):
    return SimpleNamespace(
        id=abs(hash(name)) % 100000,
        filename=name,
        storage_uri=f"gs://b/{name}",
        file_type="fastq",
        tags_json=[],
        lane=1,
        read_type=read_type,
        flowcell_id=None,
        source_run_accession=None,
    )


def _sample(sample_id: int, external_id: str, uid: str | None = None, **fields):
    base = {
        "id": sample_id,
        "uuid": uid,
        "external_id": external_id,
        "organism": "Homo sapiens",
        "donor_source": None,
        "tissue_type": None,
        "sex": None,
        "custom_fields": [],
    }
    base.update(fields)
    files = [_read(f"{external_id}_R1_001.fastq.gz", "R1"), _read(f"{external_id}_R2_001.fastq.gz", "R2")]
    return SheetSampleInput.from_sample(SimpleNamespace(**base), files)


SAMPLES = [
    _sample(2, "GUT-B", uid="00000000-0000-4000-8000-000000000002"),
    _sample(1, "GUT_A", uid="00000000-0000-4000-8000-000000000001"),
]


class TestOneResultAgreesWithTheFacades:
    @pytest.mark.parametrize("name", ["demo", "mag", "sarek", "ampliseq"])
    def test_the_preview_is_the_facades_preview(self, name):
        contract = _contract(name)
        result = SampleSheetService.build(f"nf-core/{name}", SAMPLES, {}, contract=contract)

        assert result.preview() == SampleSheetService.preview(f"nf-core/{name}", SAMPLES, {}, contract=contract)

    @pytest.mark.parametrize("name", ["mag", "sarek", "ampliseq"])
    def test_the_grid_and_the_refusal_come_from_the_same_issues(self, name):
        contract = _contract(name)
        result = SampleSheetService.build(f"nf-core/{name}", SAMPLES, {}, contract=contract)

        assert result.per_sample_inputs == SampleSheetService.per_sample_inputs(contract, SAMPLES, {})
        with pytest.raises(SamplesMissingRequiredFieldsError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, SAMPLES, {})
        assert result.refusal is not None
        assert (result.refusal.code, str(result.refusal), result.refusal.details) == (
            raised.value.code,
            str(raised.value),
            raised.value.details,
        )

    def test_a_satisfiable_sheet_has_no_refusal(self):
        result = SampleSheetService.build("nf-core/demo", SAMPLES, {}, contract=_contract("demo"))

        assert result.refusal is None
        assert result.per_sample_inputs == []
        result.raise_if_blocked()

    def test_a_pipeline_that_takes_no_samples_is_refused_as_the_check_refuses_it(self):
        contract = parse_contract(
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["taxid"],
                    "properties": {"sample": {"type": "string"}, "taxid": {"type": "integer"}},
                },
            }
        )
        result = SampleSheetService.build("nf-core/taxonomy", SAMPLES, {}, contract=contract)

        assert isinstance(result.refusal, PipelineNotSampleLaunchableError)
        with pytest.raises(PipelineNotSampleLaunchableError):
            result.raise_if_blocked()

    def test_a_handwritten_generator_is_never_judged_by_a_schema(self):
        result = SampleSheetService.build("nf-core/rnaseq", SAMPLES, {}, contract=_contract("mag"))

        assert result.refusal is None
        assert result.per_sample_inputs == []
        assert result.csv == SampleSheetService.generate_rnaseq_sheet(SAMPLES, {})

    def test_the_identity_snapshot_is_the_facades(self):
        contract = _contract("demo")
        result = SampleSheetService.build("nf-core/demo", SAMPLES, {}, contract=contract)

        assert result.identity_snapshot() == SampleSheetService.identity_snapshot(result.preview(), SAMPLES)
        assert SampleSheetService.IDENTITY_COLUMN not in result.csv

    def test_field_updates_are_decided_from_the_same_inputs(self):
        contract = _contract("sarek")
        values = {"1": {"patient": "DONOR_9"}}
        result = SampleSheetService.build("nf-core/sarek", SAMPLES, {}, contract=contract, sample_values=values)

        assert result.sample_field_updates() == SampleSheetService.sample_field_updates(contract, SAMPLES, values)
        assert result.sample_field_updates() == {1: {"donor_source": "DONOR_9"}}


class TestRows:
    def test_rows_name_their_sample_and_follow_the_given_order(self):
        result = SampleSheetService.build("nf-core/demo", SAMPLES, {}, contract=_contract("demo"))

        assert [(r.sample_id, r.external_id) for r in result.rows] == [(2, "GUT-B"), (1, "GUT_A")]
        assert result.csv.splitlines()[0].split(",") == list(result.columns)

    def test_an_unattributable_row_never_borrows_an_identity(self):
        params = {"accessions": ["SRR1"]}
        result = SampleSheetService.build("nf-core/fetchngs", SAMPLES, params)

        assert result.rows == ()
        assert result.identity_snapshot()["emitted"] == []

    def test_the_result_cannot_be_changed_after_it_is_built(self):
        result = SampleSheetService.build("nf-core/demo", SAMPLES, {}, contract=_contract("demo"))

        with pytest.raises(FrozenInstanceError):
            result.csv = "sample\nforged\n"  # type: ignore[misc]
        assert isinstance(result.rows, tuple)
        assert isinstance(result.rows[0].values, tuple)


class TestTheResultIsOneSnapshot:
    """Nothing a caller holds or is handed can change what the result says."""

    def test_changing_the_callers_contract_afterwards_changes_nothing(self):
        expected = SampleSheetService.build("nf-core/mag", SAMPLES, {}, contract=_contract("mag"))
        contract = _contract("mag")
        result = SampleSheetService.build("nf-core/mag", SAMPLES, {}, contract=contract)

        contract.required.clear()
        contract.enums.clear()

        assert result.csv == expected.csv
        assert result.issues == expected.issues
        assert result.per_sample_inputs == expected.per_sample_inputs
        assert result.refusal.details == expected.refusal.details
        with pytest.raises(SamplesMissingRequiredFieldsError):
            result.raise_if_blocked()

    def test_mutating_what_it_hands_out_changes_nothing(self):
        values = {"1": {"patient": "DONOR_9"}}
        contract = _contract("sarek")
        expected = SampleSheetService.build("nf-core/sarek", SAMPLES, {}, contract=contract, sample_values=values)
        result = SampleSheetService.build("nf-core/sarek", SAMPLES, {}, contract=contract, sample_values=values)
        assert expected.issues and expected.per_sample_inputs and expected.sample_field_updates()

        result.issues.clear()
        result.per_sample_inputs.clear()
        result.refusal.details.clear()
        result.preview()["rows"].clear()
        result.identity_snapshot()["emitted"].clear()
        result.sample_field_updates().clear()
        for omission in result.omissions:
            omission.clear()

        assert result.issues == expected.issues
        assert result.per_sample_inputs == expected.per_sample_inputs
        assert result.preview() == expected.preview()
        assert result.identity_snapshot() == expected.identity_snapshot()
        assert result.sample_field_updates() == expected.sample_field_updates()
        with pytest.raises(SamplesMissingRequiredFieldsError) as raised:
            result.raise_if_blocked()
        assert raised.value.details == expected.refusal.details

    @pytest.mark.parametrize("name", ["demo", "mag", "sarek", "ampliseq"])
    def test_the_rows_are_resolved_once_and_validated_from_them(self, name, monkeypatch):
        from app.services import sample_sheet_service as module

        calls = []
        resolve = module._sheet_rows

        def counted(*args, **kwargs):
            calls.append(1)
            return resolve(*args, **kwargs)

        monkeypatch.setattr(module, "_sheet_rows", counted)
        result = SampleSheetService.build(f"nf-core/{name}", SAMPLES, {}, contract=_contract(name))
        result.issues, result.refusal, result.per_sample_inputs
        result.identity_snapshot(), result.sample_field_updates(), result.preview()

        assert len(calls) == 1
