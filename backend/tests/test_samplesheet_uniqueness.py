"""Rows a pipeline cannot tell apart.

Step 3 asked for a sweep of schema keywords bioAF does not read. Measured across
the catalog's contracts, the answer was not the one the plan assumed:

``minLength`` and ``maxLength`` appear only as nf-schema's idiom for "or empty"
(``anyOf: [{pattern: ...}, {maxLength: 0}]`` on bacass R2, raredisease
paternal_id, rnasplice fastq_2). Enforcing them as length limits would reject
every filled value in exactly those columns, so they are deliberately NOT
enforced.

``unique`` is the one that matters, and it was unread. mag declares
``run: {unique: ["sample"]}``, meaning the run and sample pair distinguishes two
sequencing runs of one sample. A sample sequenced over two lanes produces two
rows with the same sample and no run, and nf-schema rejects that sheet after the
node has scaled up and the containers have pulled. That is the failure this
whole project exists to move earlier.

bioAF does not invent the missing value: a lane is not a sequencing run, and
writing one in would be a guess with a scientific claim inside it. It blocks and
names the column the pipeline uses to tell the rows apart.
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


def _make_file(filename: str):
    f = MagicMock()
    f.filename = filename
    f.storage_uri = f"gs://bucket/{filename}"
    f.tags_json = []
    return f


def _make_sample(sample_id: int, external_id: str, files=None, **fields):
    sample = MagicMock()
    sample.id = sample_id
    sample.external_id = external_id
    for attr in ("donor_source", "organism", "tissue_type", "treatment_condition", "sex"):
        setattr(sample, attr, fields.get(attr))
    sample._input_files = list(files or [])
    return sample


def _lanes(name: str, *lanes: str):
    files = []
    for lane in lanes:
        files.append(_make_file(f"{name}_L{lane}_R1_001.fastq.gz"))
        files.append(_make_file(f"{name}_L{lane}_R2_001.fastq.gz"))
    return files


def _flowcells(name: str, *flowcells: str):
    """Sequencing units bioAF genuinely cannot tell apart.

    Two flow cells, and no lane on either: the names carry no ``_LNNN_`` and the
    typed column is NULL, so nothing bioAF holds separates these rows. That is
    what makes this the case where the block still has to fire, now that a KNOWN
    lane fills sarek's own ``lane`` column and answers the question itself.
    """
    files = []
    for flowcell in flowcells:
        for read in ("R1", "R2"):
            f = _make_file(f"{name}_{flowcell}_{read}_001.fastq.gz")
            f.flowcell_id = flowcell
            f.lane = None
            files.append(f)
    return files


MAG_VALUES = {"group": "gut", "short_reads_platform": "ILLUMINA"}


class TestTheContractCarriesItsUniquenessRules:
    def test_it_reads_a_declared_uniqueness_group(self):
        assert _contract("mag").unique_with["run"] == ("sample",)

    def test_a_schema_declaring_none_carries_none(self):
        assert _contract("demo").unique_with == {}

    def test_unique_false_is_not_a_constraint(self):
        """bacass writes ``unique: false`` on its ID column, which states that
        the column is NOT constrained. Reading it as one would block every
        bacass launch."""
        assert "ID" not in _contract("bacass").unique_with


class TestRowsAPipelineCannotTellApart:
    def test_two_lanes_of_one_sample_block_rather_than_ship(self):
        contract = _contract("mag")
        sample = _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001", "002"))

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {"1": MAG_VALUES})

        assert "run" in raised.value.details["missing_columns"]

    def test_the_block_names_the_column_the_pipeline_uses_to_tell_them_apart(self):
        contract = _contract("mag")
        sample = _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001", "002"))

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {"1": MAG_VALUES})

        detail = raised.value.details["missing_columns"]["run"]
        assert detail["reason"] == "not_unique"
        assert [s["external_id"] for s in detail["samples"]] == ["GUT_A"]

    def test_one_lane_per_sample_is_unaffected(self):
        contract = _contract("mag")
        samples = [
            _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001")),
            _make_sample(2, "GUT_B", files=_lanes("GUT_B", "001")),
        ]

        SampleSheetService.check_contract_satisfiable(contract, samples, {}, {"1": MAG_VALUES, "2": MAG_VALUES})

    def test_stating_the_distinguishing_value_unblocks_it(self):
        """The entry grid is where this gets answered. bioAF does not answer it
        itself: a lane is not a sequencing run."""
        contract = _contract("mag")
        sample = _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001", "002"))

        # One value per sample cannot distinguish two rows OF that sample, so
        # this still blocks. Recorded as a known limit of the current grid.
        with pytest.raises(DomainError):
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {"1": {**MAG_VALUES, "run": "1"}})

    def test_a_pipeline_with_no_uniqueness_rule_takes_multi_lane_rows(self):
        """rnaseq and friends merge lanes by design. Nothing changes for them."""
        contract = _contract("demo")
        sample = _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001", "002"))

        SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {})


class TestTheEmptyStringIdiomIsNotALengthLimit:
    def test_a_filled_value_is_not_rejected_by_the_empty_branch(self):
        """bacass R2 declares ``anyOf: [{pattern}, {maxLength: 0}]``, which says
        the column may be empty. Reading maxLength as a limit would reject every
        real path in it."""
        contract = _contract("bacass")
        sample = _make_sample(1, "GUT_A", files=_lanes("GUT_A", "001"))

        sheet = SampleSheetService.generate_from_contract(contract, [sample], {}, {"1": {"GenomeSize": "5.4m"}})

        assert "GUT_A_L001_R2_001.fastq.gz" in sheet


class TestTheSheetLevelSpellingIsReadToo:
    """``uniqueEntries`` is how most of the catalog states this, and it is
    declared in three different places. Reading only one spelling would enforce
    the rule for mag and silently ignore it for ampliseq, sarek and taxprofiler,
    which is worse than not reading it at all: it would look implemented."""

    def test_a_root_level_group_is_read(self):
        assert ("sample", "run") in _contract("mag").unique_entries

    def test_a_group_inside_a_root_allOf_is_read(self):
        entries = _contract("ampliseq").unique_entries
        assert ("sample",) in entries and ("sampleID",) in entries

    def test_several_groups_inside_one_allOf_are_all_read(self):
        entries = _contract("taxprofiler").unique_entries
        assert ("sample", "run_accession") in entries
        assert ("fastq_1",) in entries

    def test_a_group_on_items_is_read(self):
        assert ("lane", "patient", "sample") in _contract("sarek").unique_entries

    def test_whole_row_uniqueness_is_read(self):
        assert _contract("funcscan").unique_rows is True

    def test_a_schema_declaring_none_carries_none(self):
        assert _contract("demo").unique_entries == ()
        assert _contract("demo").unique_rows is False


class TestAPipelineThatWantsOneRowPerSample:
    def test_two_lanes_block_for_ampliseq_which_names_only_the_sample(self):
        """ampliseq declares uniqueEntries ["sample"], so a sample sequenced over
        two lanes would appear twice and be rejected. It is installed on the demo,
        which is how this spelling was found: the in-repo fixtures alone would
        have missed it."""
        contract = _contract("ampliseq")
        sample = _make_sample(1, "SAMPLEA", files=_lanes("SAMPLEA", "001", "002"))

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {})

        gap = raised.value.details["missing_columns"]
        assert any(detail.get("reason") == "not_unique" for detail in gap.values())

    def test_one_lane_per_sample_launches_ampliseq_unchanged(self):
        contract = _contract("ampliseq")
        samples = [
            _make_sample(1, "SAMPLEA", files=_lanes("SAMPLEA", "001")),
            _make_sample(2, "SAMPLEB", files=_lanes("SAMPLEB", "001")),
        ]

        SampleSheetService.check_contract_satisfiable(contract, samples, {}, {})

    def test_it_names_the_column_that_can_break_the_tie(self):
        """sarek is told apart by ("lane", "patient", "sample"). `patient` is
        already filled from the sample's donor and cannot break a tie between two
        rows of one sample; `lane` is what the scientist would state. Naming the
        wrong one sends them to change a value that is already correct.

        The sample is two FLOW CELLS with no lane on either, because a known lane
        now fills sarek's ``lane`` column itself and there is no tie left to
        break; see ``test_samplesheet_sequencing_facts``. Two rows bioAF cannot
        separate is the case this block still exists for.
        """
        contract = _contract("sarek")
        sample = _make_sample(
            1, "GUT_A", files=_flowcells("GUT_A", "HFWFVDMXX", "HJKLMDMXX"), donor_source="DONOR_1", sex="XX"
        )

        with pytest.raises(DomainError) as raised:
            SampleSheetService.check_contract_satisfiable(contract, [sample], {}, {})

        gaps = raised.value.details["missing_columns"]
        assert gaps.get("lane", {}).get("reason") == "not_unique"
        assert gaps.get("patient", {}).get("reason") != "not_unique"
