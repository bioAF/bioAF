"""The numeric and FastQC-section helpers the tailored QC templates share.

bulk RNA-seq, ChIP-seq and ATAC-seq each carried their own copy of five helpers
(``_num``, ``_field``, ``_numbers``, ``_mean``, ``_pick_raw_fastqc``). The
parsed bodies were identical, so they now live once in
``app.services.qc.template_helpers``.

The generic registry (``qc/multiqc_registry.py``) answers similar questions with
DIFFERENT rules, and this extraction deliberately does not merge them: which
rules a QC number should follow is a scientific decision for the owner (plan 16,
delivery 4B). The registry cases below pin where the two disagree, so a later
reconciliation has to change a test on purpose rather than drift.

Every case uses literal MultiQC-shaped dicts; nothing here reads storage.
"""

import ast
import json
import math
from pathlib import Path

import pytest

from app.services.qc import multiqc_registry as registry
from app.services.qc import template_helpers as th
from app.services.qc.templates import atacseq, bulk_rnaseq, chipseq

ROOT = Path(__file__).resolve().parent
REAL = ROOT / "fixtures" / "multiqc"
PINNED = json.loads((ROOT / "fixtures" / "multiqc_tailored_outputs.json").read_text())
TEMPLATES = {"bulk_rnaseq": bulk_rnaseq, "chipseq": chipseq, "atacseq": atacseq}


@pytest.mark.parametrize("key", sorted(PINNED))
def test_every_tailored_template_is_unchanged_on_every_real_report(key):
    """Pinned before the extraction, from the five real reports, for all three templates."""
    template, report = key.split("/")
    assert TEMPLATES[template].read_multiqc_metrics((REAL / report).read_text()) == PINNED[key]


def test_each_helper_has_one_implementation():
    shared = {"_num", "_field", "_numbers", "_mean", "_pick_raw_fastqc"}
    for name in TEMPLATES:
        tree = ast.parse((ROOT.parent / "app" / "services" / "qc" / "templates" / f"{name}.py").read_text())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert not (defined & shared), f"{name} still defines {sorted(defined & shared)}"


class TestNumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(12, 12.0), (1.5, 1.5), ("1,234", 1234.0), (" 7 ", 7.0), ("12.5", 12.5)],
    )
    def test_numbers_and_numeric_text_are_read(self, raw, expected):
        assert th.number(raw) == expected

    @pytest.mark.parametrize("raw", [True, False, None, "n/a", "", [1], {"v": 1}])
    def test_anything_else_is_not_a_number(self, raw):
        assert th.number(raw) is None

    def test_non_finite_values_pass_through_as_the_templates_always_read_them(self):
        """Recorded, not endorsed: the registry refuses these (see TestWhereTheRegistryDiffers)."""
        assert th.number(float("inf")) == math.inf
        assert math.isnan(th.number("nan"))


class TestFirstNumber:
    def test_the_first_key_with_a_number_wins(self):
        assert (
            th.first_number({"total_sequences": "9", "Total Sequences": 5}, "Total Sequences", "total_sequences") == 5.0
        )

    def test_a_key_without_a_number_is_skipped(self):
        assert th.first_number({"a": "n/a", "b": 3}, "a", "b") == 3.0

    def test_none_when_no_key_has_one(self):
        assert th.first_number({"a": None}, "a", "b") is None


class TestNumbers:
    def test_one_value_per_sample_record_and_malformed_records_are_skipped(self):
        section = {"S1": {"x": 1}, "S2": {"x": "2"}, "S3": "not a record", "S4": {"y": 9}}
        assert th.numbers(section, "x") == [1.0, 2.0]

    def test_mean(self):
        assert th.mean([1.0, 2.0, 6.0]) == 3.0


class TestPickRawFastqc:
    def test_the_section_with_more_reads_is_raw_whatever_its_suffix(self):
        trimmed = {"S1": {"Total Sequences": 90}}
        raw_ = {"S1": {"Total Sequences": 100}}
        assert th.pick_raw_fastqc({"multiqc_fastqc": trimmed, "multiqc_fastqc_1": raw_}) is raw_

    def test_totals_read_the_lowercase_alias_and_numeric_text(self):
        a = {"S1": {"total_sequences": "1,000"}}
        b = {"S1": {"Total Sequences": 999}}
        assert th.pick_raw_fastqc({"multiqc_fastqc": b, "multiqc_fastqc_1": a}) is a

    def test_equal_totals_keep_the_first_section(self):
        first = {"S1": {"Total Sequences": 10}}
        second = {"S1": {"Total Sequences": 10}}
        assert th.pick_raw_fastqc({"multiqc_fastqc": first, "multiqc_fastqc_1": second}) is first

    def test_no_fastqc_section_is_an_empty_section(self):
        assert th.pick_raw_fastqc({"multiqc_star": {"S1": {}}}) == {}

    def test_matching_is_a_case_sensitive_prefix(self):
        """Recorded: a FastQ Screen section shares the prefix and competes; the registry excludes it."""
        screen = {"S1": {"Total Sequences": 500}}
        fastqc = {"S1": {"Total Sequences": 100}}
        assert th.pick_raw_fastqc({"multiqc_fastqc": fastqc, "multiqc_fastqc_screen": screen}) is screen
        assert th.pick_raw_fastqc({"MULTIQC_FASTQC": fastqc}) == {}


class TestWhereTheRegistryDiffers:
    """The generic registry on the same inputs. Each is a 4B decision, not a defect fixed here."""

    def test_numeric_text_is_not_a_number_to_the_registry(self):
        assert registry._numbers({"S1": {"x": "1,234"}}, "x") == []

    def test_non_finite_values_are_not_numbers_to_the_registry(self):
        assert registry._numbers({"S1": {"x": float("inf")}, "S2": {"x": float("nan")}}, "x") == []

    def test_the_registry_reads_one_exact_column_with_no_alias(self):
        a = {"S1": {"total_sequences": 1000}}
        b = {"S1": {"Total Sequences": 999}}
        assert registry.select_sections({"multiqc_fastqc": b, "multiqc_fastqc_1": a}, "fastqc") == [b]

    def test_equal_totals_pick_the_greatest_section_id(self):
        first = {"S1": {"Total Sequences": 10}}
        second = {"S1": {"Total Sequences": 10}}
        assert registry.select_sections({"multiqc_fastqc": first, "multiqc_fastqc_1": second}, "fastqc") == [second]

    def test_fastq_screen_is_another_module_and_case_is_folded(self):
        screen = {"S1": {"Total Sequences": 500}}
        fastqc = {"S1": {"Total Sequences": 100}}
        assert registry.select_sections({"multiqc_fastqc": fastqc, "multiqc_fastqc_screen": screen}, "fastqc") == [
            fastqc
        ]
        assert registry.select_sections({"MULTIQC_FASTQC": fastqc}, "fastqc") == [fastqc]
