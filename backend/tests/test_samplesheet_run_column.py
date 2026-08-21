"""A `run` column filled from the sequencing run, and an honest block when it cannot be.

bioAF used to block a multi-lane mag or ampliseq launch and then hand the
scientist a `run` field to fill that could not unblock it, whatever they typed.
ampliseq's was the harmful one: it asked them to change the sample's own name,
so complying corrupted the LIMS record and still did not launch.

Two halves, and they are not in tension:

**Fill it where bioAF holds the fact.** A flow cell IS a sequencing run in
Illumina terms (``instrument:runNumber:flowcellID:lane``), so a row off a known
flow cell can report its run rather than asking for one. Sourced per row from
``source_run_accession`` first, then ``flowcell_id``.

**Block honestly where no value could ever help.** Two lanes of ONE flow cell
are the same sequencing run, so any value that separates them is a lane wearing
a run's name: the fiction bioAF refused to commit itself, and it must not be
outsourced to the scientist either. ampliseq is always this case, because its
rule is on the sample ALONE. Both block with a message naming the real remedy,
and neither offers a column in the entry grid.

The line stays the project's governing rule: a wrong mapping is worse than a
missing one, and a wrong ANSWER is worse than a missing one. Where the flow cell
is unknown bioAF still asks, because it genuinely does not know whether two rows
came off one run.

These assert the emitted sheet, the launch decision and the words a scientist
reads, not how any of the three is built.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exceptions import DomainError
from app.services.sample_sheet_service import SampleSheetService
from app.services.samplesheet_schema import parse_contract

FIXTURES = Path(__file__).parent / "fixtures" / "schema_input"

# What mag requires before anything here can be reached. Neither is the subject.
MAG_VALUES = {"group": "gut", "short_reads_platform": "ILLUMINA"}


def _contract(name: str):
    return parse_contract(json.loads((FIXTURES / f"{name}.json").read_text()))


def _make_file(filename: str, **identity):
    """A file whose sequencing identity is only what the test states.

    Every typed column is set explicitly, because a MagicMock auto-vivifies any
    attribute into a truthy object and an unset `flowcell_id` would otherwise
    read as a flow cell.
    """
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.tags_json = []
    for column in ("lane", "read_type", "flowcell_id", "index_sequence", "source_run_accession"):
        setattr(f, column, identity.get(column))
    return f


def _make_sample(sample_id: int, external_id: str, files=None):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, None)
    sample._input_files = list(files or [])
    return sample


def _pair(name: str, **identity):
    """One sequencing unit of a sample: its R1 and its R2."""
    return [
        _make_file(f"{name}_R1_001.fastq.gz", read_type="R1", **identity),
        _make_file(f"{name}_R2_001.fastq.gz", read_type="R2", **identity),
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


def _blocked(contract, samples, values=None):
    """The DomainError a launch of these samples raises, or a failure if it does not."""
    with pytest.raises(DomainError) as raised:
        SampleSheetService.check_contract_satisfiable(contract, samples, {}, values or {})
    return raised.value


class TestARunIsFilledFromTheSequencingRun:
    """The half of this that unblocks working data."""

    def test_two_flow_cells_carry_their_own_runs(self):
        """The headline. Two flow cells are two sequencing runs, and bioAF holds
        which is which, so it says so instead of asking."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("GUT_A_L001b", flowcell_id="HTTJ5DSX7", lane=1),
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert _column(csv_text, "run") == ["HLK3VDSX7", "HTTJ5DSX7"]

    def test_two_flow_cells_now_launch(self):
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("GUT_A_L001b", flowcell_id="HTTJ5DSX7", lane=1),
        )

        SampleSheetService.check_contract_satisfiable(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

    def test_two_archive_runs_carry_their_own_runs(self):
        """A fetched sample's sibling runs have no flow cell and no lane. The
        accession is the run, and it is what separates them."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("SRR111", source_run_accession="SRR111") + _pair("SRR222", source_run_accession="SRR222"),
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert _column(csv_text, "run") == ["SRR111", "SRR222"]

    def test_two_archive_runs_now_launch(self):
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("SRR111", source_run_accession="SRR111") + _pair("SRR222", source_run_accession="SRR222"),
        )

        SampleSheetService.check_contract_satisfiable(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

    def test_the_accession_wins_over_the_flow_cell(self):
        """A row carrying both. The accession names a run the archive published;
        the flow cell is bioAF's own reading of the header, so the published one
        is the more faithful answer to "which run"."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("SRR111", flowcell_id="HLK3VDSX7", lane=1, source_run_accession="SRR111")
            + _pair("SRR222", flowcell_id="HTTJ5DSX7", lane=1, source_run_accession="SRR222"),
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert _column(csv_text, "run") == ["SRR111", "SRR222"]

    def test_one_lane_of_one_flow_cell_gains_a_run_column(self):
        """Filled always when the fact is known, matching what sarek's `lane`
        does. One row stays one group, so a column it never carried cannot
        change mag's grouping."""
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1))

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert _column(csv_text, "run") == ["HLK3VDSX7"]
        assert len(_rows(csv_text)) == 2  # header plus the one row

    def test_one_lane_of_one_flow_cell_launches(self):
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1))

        SampleSheetService.check_contract_satisfiable(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

    def test_a_pre_merged_fastq_gains_no_run_column(self):
        """Nothing is known, so nothing is written. The optional column is
        simply not emitted, exactly as before."""
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A"))

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert "run" not in _rows(csv_text)[0]

    def test_a_stated_run_still_wins(self):
        """The scientist's own answer outranks the measurement, as it does for
        every other column."""
        sample = _make_sample(1, "GUT_A", files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1))

        csv_text = SampleSheetService.generate_from_contract(
            _contract("mag"), [sample], {}, {"1": {**MAG_VALUES, "run": "RUN_7"}}
        )

        assert _column(csv_text, "run") == ["RUN_7"]

    def test_a_lane_never_reaches_a_run_column(self):
        """The half of decision 7 that stands. A lane is not a sequencing run,
        so a lane number must never be written into one, and no amount of
        widening the fill may change that."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", lane=1) + _pair("GUT_A_L002", lane=2),
        )

        csv_text = SampleSheetService.generate_from_contract(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert _column(csv_text, "run") in ([], ["", ""])


class TestTwoLanesOfOneFlowCellBlockWithTheRemedy:
    """The half that stops misdirecting. These rows came off ONE sequencing run,
    so no value of `run` could tell them apart, and bioAF must say so rather than
    offer a field."""

    def _sample(self):
        return _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("GUT_A_L002", flowcell_id="HLK3VDSX7", lane=2),
        )

    def test_it_still_blocks(self):
        error = _blocked(_contract("mag"), [self._sample()], {"1": MAG_VALUES})

        assert error.details["missing_columns"]["run"]["reason"] == "not_unique"

    def test_the_message_names_the_flow_cell_the_lanes_and_the_remedy(self):
        """The copy is doing the work the anchor choice used to do. If this ever
        reverts to "supply a run", it sends the scientist to change a value bioAF
        wrote itself."""
        error = _blocked(_contract("mag"), [self._sample()], {"1": MAG_VALUES})
        gap = error.details["missing_columns"]["run"]

        assert gap["remedy"] == "merge_reads"
        assert gap["repeated"] == [{"run": "HLK3VDSX7", "source": "flowcell", "lanes": ["1", "2"]}]

    def test_the_summary_does_not_ask_for_a_value(self):
        """The sentence a scientist reads first. "needs run to tell some rows
        apart" is exactly the misdirection this item removes."""
        error = _blocked(_contract("mag"), [self._sample()], {"1": MAG_VALUES})

        assert "one sequencing run" in str(error)
        assert "needs run to tell some rows apart" not in str(error)

    def test_the_grid_no_longer_offers_a_column_that_cannot_clear_the_block(self):
        """The defect itself. Whatever they typed, the launch stayed blocked."""
        specs = SampleSheetService.per_sample_inputs(_contract("mag"), [self._sample()], {}, {"1": MAG_VALUES})

        assert [s["name"] for s in specs if s["name"] == "run"] == []

    def test_two_flow_cells_with_two_lanes_each_block_naming_both(self):
        """Filling the column did not make the collision go away: each flow cell
        still contributes two rows that share it."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("A_L001", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("A_L002", flowcell_id="HLK3VDSX7", lane=2)
            + _pair("B_L001", flowcell_id="HTTJ5DSX7", lane=1)
            + _pair("B_L002", flowcell_id="HTTJ5DSX7", lane=2),
        )

        gap = _blocked(_contract("mag"), [sample], {"1": MAG_VALUES}).details["missing_columns"]["run"]

        assert [entry["run"] for entry in gap["repeated"]] == ["HLK3VDSX7", "HTTJ5DSX7"]
        assert [entry["lanes"] for entry in gap["repeated"]] == [["1", "2"], ["1", "2"]]

    def test_one_archive_run_over_two_lanes_names_the_run_not_a_flow_cell(self):
        """The same shape sourced from the accession. Calling it a flow cell
        there would name something bioAF did not read."""
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("SRR111_L001", lane=1, source_run_accession="SRR111")
            + _pair("SRR111_L002", lane=2, source_run_accession="SRR111"),
        )

        gap = _blocked(_contract("mag"), [sample], {"1": MAG_VALUES}).details["missing_columns"]["run"]

        assert gap["repeated"] == [{"run": "SRR111", "source": "accession", "lanes": ["1", "2"]}]


class TestAnUnknownFlowCellStillAsks:
    """bioAF must not claim two rows came off one run when it never read a flow
    cell. Two lanes with no flow cell may well be two different runs, and only
    the scientist knows."""

    def test_the_column_is_still_offered(self):
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", lane=1) + _pair("GUT_A_L002", lane=2),
        )

        specs = SampleSheetService.per_sample_inputs(_contract("mag"), [sample], {}, {"1": MAG_VALUES})

        assert "run" in [s["name"] for s in specs]

    def test_no_remedy_is_asserted(self):
        sample = _make_sample(
            1,
            "GUT_A",
            files=_pair("GUT_A_L001", lane=1) + _pair("GUT_A_L002", lane=2),
        )

        gap = _blocked(_contract("mag"), [sample], {"1": MAG_VALUES}).details["missing_columns"]["run"]

        assert gap.get("remedy") is None


class TestAmpliseqTakesOneRowPerSample:
    """Its rule is ``uniqueEntries: ["sample"]``, on the sample ALONE, so no
    value of `run` can ever separate its rows. Pointing at `run` would be a
    column that could never help; pointing at `sample` asks them to rename their
    own sample, which corrupts the LIMS record and still does not launch."""

    def _sample(self):
        return _make_sample(
            1,
            "SAMPLEA",
            files=_pair("SAMPLEA_L001", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("SAMPLEA_L002", flowcell_id="HLK3VDSX7", lane=2),
        )

    def test_it_still_blocks(self):
        gaps = _blocked(_contract("ampliseq"), [self._sample()]).details["missing_columns"]

        assert any(detail.get("reason") == "not_unique" for detail in gaps.values())

    def test_the_remedy_is_one_row_per_sample(self):
        gaps = _blocked(_contract("ampliseq"), [self._sample()]).details["missing_columns"]

        assert [g["remedy"] for g in gaps.values() if g.get("reason") == "not_unique"] == ["one_row_per_sample"]

    def test_the_summary_says_one_row_per_sample(self):
        error = _blocked(_contract("ampliseq"), [self._sample()])

        assert "one row per sample" in str(error)

    def test_the_grid_offers_nothing_that_cannot_clear_the_block(self):
        """Whatever the anchor column is, it must not be asked for: no value of
        it separates two rows of one sample."""
        specs = SampleSheetService.per_sample_inputs(_contract("ampliseq"), [self._sample()], {}, {})

        assert [s["name"] for s in specs if s.get("reason") == "not_unique"] == []

    def test_two_flow_cells_are_still_one_row_per_sample(self):
        """ "ampliseq is always this case." Separate runs do not rescue it,
        because the rule was never about the run."""
        sample = _make_sample(
            1,
            "SAMPLEA",
            files=_pair("SAMPLEA_a", flowcell_id="HLK3VDSX7", lane=1)
            + _pair("SAMPLEA_b", flowcell_id="HTTJ5DSX7", lane=1),
        )

        gaps = _blocked(_contract("ampliseq"), [sample]).details["missing_columns"]

        assert [g["remedy"] for g in gaps.values() if g.get("reason") == "not_unique"] == ["one_row_per_sample"]
