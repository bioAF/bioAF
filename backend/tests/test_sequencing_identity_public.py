"""The public reading of a file's sequencing identity.

One rule decides what lane, read and unit a file belongs to: the typed column
wins, then the legacy ``lane:``/``read:`` tag files written before migration 119
carry, then the Illumina filename. Read groups and sheet pairing both answer
"which unit is this file?", and used to share that answer by importing the sheet
builder's private helpers. These pin the rule as a public contract of the
sequencing domain, so either consumer can change without the other noticing.

Fixtures are plain objects on purpose: a MagicMock invents a truthy value for any
attribute, which is exactly the input the reader must refuse.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import sequencing_identity as si


def _file(**kw):
    attrs = {
        "filename": "",
        "tags_json": [],
        "lane": None,
        "read_type": None,
        "flowcell_id": None,
        "source_run_accession": None,
    }
    attrs.update(kw)
    return SimpleNamespace(**attrs)


class TestReadType:
    def test_the_typed_column_beats_the_tag_and_the_name(self):
        f = _file(read_type="R2", tags_json=["read:R1"], filename="x_R1_001.fastq.gz")
        assert si.read_type(f) == "R2"

    def test_a_typed_value_that_is_not_a_read_code_is_ignored(self):
        f = _file(read_type="RI", tags_json=["read:R1"])
        assert si.read_type(f) == "R1"

    def test_the_tag_beats_the_name(self):
        f = _file(tags_json=["read:I1"], filename="x_R1_001.fastq.gz")
        assert si.read_type(f) == "I1"

    def test_the_name_is_the_last_resort(self):
        assert si.read_type(_file(filename="S_S1_L001_R2_001.fastq.gz")) == "R2"

    def test_nothing_known_is_none(self):
        assert si.read_type(_file(filename="merged.fastq.gz")) is None


class TestLane:
    def test_the_typed_column_wins(self):
        assert si.lane(_file(lane=3, tags_json=["lane:1"], filename="x_L002_R1_001.fastq.gz")) == 3

    def test_a_typed_zero_is_not_a_lane_and_does_not_fall_through(self):
        assert si.lane(_file(lane=0, tags_json=["lane:1"])) is None

    @pytest.mark.parametrize("tag", ["lane:1", "lane:001"])
    def test_both_legacy_spellings_are_one_lane(self, tag):
        assert si.lane(_file(tags_json=[tag])) == 1

    @pytest.mark.parametrize("tag", ["lane:0", "lane:x", "lane:"])
    def test_a_tag_that_is_not_a_lane_is_none(self, tag):
        assert si.lane(_file(tags_json=[tag], filename="x_L002_R1_001.fastq.gz")) is None

    def test_a_boolean_is_not_a_lane(self):
        assert si.lane(_file(lane=True, filename="x_L004_R1_001.fastq.gz")) == 4

    def test_the_filename_lane(self):
        assert si.lane(_file(filename="x_L002_R1_001.fastq.gz")) == 2
        assert si.lane(_file(filename="x_L000_R1_001.fastq.gz")) is None


class TestSequencingUnit:
    def test_the_same_lane_on_two_flow_cells_is_two_units(self):
        a = _file(lane=1, flowcell_id="HAAA")
        b = _file(lane=1, flowcell_id="HBBB")
        assert si.sequencing_unit(a) != si.sequencing_unit(b)

    def test_archive_runs_are_told_apart_by_accession(self):
        a = _file(source_run_accession="SRR1")
        b = _file(source_run_accession="SRR2")
        assert si.sequencing_unit(a) != si.sequencing_unit(b)

    def test_nothing_known_is_the_one_implicit_unit(self):
        assert si.sequencing_unit(_file(filename="merged.fastq.gz")) == si.UNKNOWN_UNIT

    def test_the_unit_reads_back_as_its_facts(self):
        f = _file(lane=2, flowcell_id="HAAA", source_run_accession="SRR9")
        assert si.sequencing_unit(f) == ("HAAA", 2, "SRR9")
        assert si.flowcell_id(f) == "HAAA"
        assert si.run_accession(f) == "SRR9"

    def test_untyped_identity_values_are_not_trusted(self):
        f = _file(flowcell_id=7, source_run_accession=["SRR1"])
        assert si.flowcell_id(f) is None
        assert si.run_accession(f) is None


def test_read_groups_do_not_reach_into_the_sheet_builder():
    """Read groups and sheet pairing share the sequencing rule through its public module."""
    source = (Path(__file__).resolve().parent.parent / "app" / "services" / "read_groups.py").read_text()
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "app.services.sample_sheet_service" not in imported
