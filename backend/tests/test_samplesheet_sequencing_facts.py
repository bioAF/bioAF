"""A distinguishing column filled from the file's own sequencing identity.

Two rows of one sample exist because the sample was sequenced more than once.
The pipeline asks which is which, in a column of its own naming, and until now
bioAF always answered "I cannot say" and blocked the launch.

That answer was right while a lane was a string in a tag array. Migration 119
made it a typed fact on the file, so for the columns whose MEANING is that fact,
bioAF is now reporting a measurement rather than guessing:

    sarek       lane            filled from File.lane
    taxprofiler run_accession   filled from File.source_run_accession
    mag         run             ASKED. A sequencing run is not a lane
    ampliseq    run             ASKED, for the same reason

The line is the project's governing rule: a wrong mapping is worse than a
missing one. Filling sarek's `lane` reports what bioAF measured; filling mag's
`run` with a lane number would be a scientific claim bioAF has no basis for.

These assert the emitted sheet and the launch decision, not how either is built.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import DomainError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_file(filename: str, **identity):
    """A file whose sequencing identity is only what the test states.

    Every typed column is set explicitly, because a MagicMock auto-vivifies any
    attribute into a truthy object and an unset `lane` would otherwise read as a
    lane.
    """
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.tags_json = []
    for column in ("lane", "read_type", "flowcell_id", "index_sequence", "source_run_accession"):
        setattr(f, column, identity.get(column))
    return f


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _pair(name: str, suffix: str = "", **identity):
    """One sequencing unit of a sample: its R1 and its R2."""
    return [
        _make_file(f"{name}{suffix}_R1_001.fastq.gz", read_type="R1", **identity),
        _make_file(f"{name}{suffix}_R2_001.fastq.gz", read_type="R2", **identity),
    ]


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().splitlines()]


def _column(csv_text: str, name: str) -> list[str]:
    """Every row's value for one column, in emitted order."""
    rows = _rows(csv_text)
    if name not in rows[0]:
        return []
    at = rows[0].index(name)
    return [row[at] for row in rows[1:]]


SAREK_FIELDS = {"donor_source": "DONOR_1", "sex": "XX"}


class TestSarekLaneIsReported:
    def test_two_lanes_of_one_sample_carry_their_own_lane_numbers(self):
        """The headline: bioAF holds the lane as a typed fact, so it says so."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_S1_L001", lane=1) + _pair("GUT_A_S1_L002", lane=2),
            **SAREK_FIELDS,
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], {})

        assert _column(csv_text, "lane") == ["1", "2"]

    def test_a_sample_sequenced_over_two_lanes_now_launches(self):
        """The rows are told apart by the value the pipeline asked for, so the
        block that used to stop this launch has its answer."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_S1_L001", lane=1) + _pair("GUT_A_S1_L002", lane=2),
            **SAREK_FIELDS,
        )

        SampleSheetService.check_contract_satisfiable(_contract("sarek"), [sample], {}, {})

    def test_the_typed_lane_wins_over_the_filename(self):
        """A filename is a hint; the typed column is the fact. A file moved or
        renamed after ingest keeps the lane it was sequenced in."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_S1_L001", lane=7),
            **SAREK_FIELDS,
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], {})

        assert _column(csv_text, "lane") == ["7"]

    def test_a_pre_merged_fastq_carries_no_lane_at_all(self):
        """A CRO's merged FASTQs have no lane, and inventing one would state
        something false. The optional column is simply not emitted."""
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A"), **SAREK_FIELDS)

        csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], {})

        assert "lane" not in _rows(csv_text)[0]

    def test_a_stated_lane_still_wins(self):
        """The scientist's own answer outranks the measurement, exactly as it
        does for every other column."""
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A_S1_L001", lane=1), **SAREK_FIELDS)

        csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), [sample], {}, {"1": {"lane": "5"}})

        assert _column(csv_text, "lane") == ["5"]


class TestARunIsNotALane:
    """The half of decision 7 that keeps asking. These pipelines name a
    sequencing RUN, and a lane is not one."""

    def test_mag_still_blocks_on_two_lanes_of_one_sample(self):
        contract = _contract("mag")
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_S1_L001", lane=1) + _pair("GUT_A_S1_L002", lane=2),
        )

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(
                contract, [sample], {}, {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}
            )

        assert raised.value.details["missing_columns"].get("run", {}).get("reason") == "not_unique"

    def test_ampliseq_still_blocks_on_two_lanes_of_one_sample(self):
        contract = _contract("ampliseq")
        sample = _make_sample(
            1,
            "SAMPLEA",
            files=_pair("SAMPLEA_S1_L001", lane=1) + _pair("SAMPLEA_S1_L002", lane=2),
        )

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {})

        gaps = raised.value.details["missing_columns"]
        assert any(detail.get("reason") == "not_unique" for detail in gaps.values())

    def test_a_lane_never_reaches_a_run_column(self):
        """Stated positively, so a later refactor that widens the fill has to
        break this test to do it."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_S1_L001", lane=1) + _pair("GUT_A_S1_L002", lane=2),
        )

        csv_text = SampleSheetService.generate_from_contract(
            _contract("mag"), [sample], {}, {"1": {"group": "gut", "short_reads_platform": "ILLUMINA"}}
        )

        assert _column(csv_text, "run") in ([], ["", ""])


class TestTaxprofilerRunAccession:
    def test_sibling_runs_carry_their_own_accessions(self):
        """A fetched sample's files each come from a real archive run, and that
        accession is exactly what this column names."""
        sample = _make_sample(
            1,
            "SAMPLE_A",
            files=(_pair("SRR111", source_run_accession="SRR111") + _pair("SRR222", source_run_accession="SRR222")),
        )

        csv_text = SampleSheetService.generate_from_contract(
            _contract("taxprofiler"), [sample], {"instrument_platform": "ILLUMINA"}
        )

        assert _column(csv_text, "run_accession") == ["SRR111", "SRR222"]

    def test_two_fetched_runs_of_one_sample_now_launch(self):
        sample = _make_sample(
            1,
            "SAMPLE_A",
            files=(_pair("SRR111", source_run_accession="SRR111") + _pair("SRR222", source_run_accession="SRR222")),
        )

        SampleSheetService.check_contract_satisfiable(
            _contract("taxprofiler"), [sample], {"instrument_platform": "ILLUMINA"}, {}
        )

    def test_a_file_with_no_accession_falls_back_to_the_sample_name(self):
        """Unchanged behaviour for the uploaded case: bioAF carries one row per
        sample, so the sample's own name stands in for its run."""
        sample = _make_sample(1, "SAMPLE_A", files=_pair("SAMPLE_A"))

        csv_text = SampleSheetService.generate_from_contract(
            _contract("taxprofiler"), [sample], {"instrument_platform": "ILLUMINA"}
        )

        assert _column(csv_text, "run_accession") == ["SAMPLE_A"]


class TestTheFillIsPerRowNotPerSample:
    def test_each_sample_keeps_its_own_lanes(self):
        """Two samples, each over two lanes. A per-sample fill would put one
        sample's lane on the other's rows."""
        samples = [
            _make_sample(
                1,
                "GUT_A",
                files=_pair("GUT_A_S1_L001", lane=1) + _pair("GUT_A_S1_L002", lane=2),
                **SAREK_FIELDS,
            ),
            _make_sample(
                2,
                "GUT_B",
                files=_pair("GUT_B_S1_L003", lane=3) + _pair("GUT_B_S1_L004", lane=4),
                **SAREK_FIELDS,
            ),
        ]

        csv_text = SampleSheetService.generate_from_contract(_contract("sarek"), samples, {})

        assert _column(csv_text, "lane") == ["1", "2", "3", "4"]
        assert _column(csv_text, "sample") == ["GUT_A", "GUT_A", "GUT_B", "GUT_B"]
