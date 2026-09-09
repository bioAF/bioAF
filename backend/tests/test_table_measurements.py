"""change_7.1 section 7: measure a table for what the paper actually claims about it.

The first pass hard-coded |log2FC| > 1 and > 2. Those are Groff's cutoffs, and a constant chosen
from one paper is a paper-specific rule living in production code. A different paper claiming a
1.5-fold cutoff got two numbers it never mentioned and not the one it did.

Groff also claims 146 sex-linked genes. No threshold on a fold-change column can produce that: it
needs the `chr` column read. Headers and row totals cannot verify a claim about a column's
contents.
"""

from app.services.supplement_inventory import measure_table

# Chromosomes repeat across rows, as they do in any real differential table. A column whose values
# are all distinct is an identifier, not a category, and the measurement skips those on purpose.
_RESULTS = (
    "info\tbaseMean\tlog2FoldChange\tpvalue\tpadj\tchr\n"
    "GENE1\t100\t-3.5\t0.001\t0.01\tchrX\n"
    "GENE2\t200\t2.5\t0.001\t0.01\tchrY\n"
    "GENE3\t300\t1.2\t0.02\t0.04\tchrX\n"
    "GENE4\t400\t-0.5\t0.03\t0.04\tchr1\n"
).encode()


class TestThresholdsComeFromTheClaims:
    def test_a_claimed_cutoff_is_the_one_measured(self):
        measured = measure_table(_RESULTS, thresholds=[1.5])
        assert measured["threshold_splits"] == {"abs_log2fc>1.5": 2}

    def test_the_sex_linked_count_comes_from_the_chromosome_column(self):
        """Groff claims 146 sex-linked genes. No fold-change threshold can produce that number;
        only reading the chromosome column can."""
        counts = measure_table(_RESULTS, thresholds=[])["category_counts"]["chr"]
        assert counts["chrX"] + counts["chrY"] == 3

    def test_several_claimed_cutoffs_are_all_measured(self):
        measured = measure_table(_RESULTS, thresholds=[1.0, 3.0])
        assert measured["threshold_splits"] == {"abs_log2fc>1": 3, "abs_log2fc>3": 1}

    def test_no_claimed_cutoff_measures_no_split(self):
        """Inventing a cutoff the paper never named produces a number nobody asked for and hides
        the one they did."""
        measured = measure_table(_RESULTS, thresholds=[])
        assert measured.get("threshold_splits") in (None, {})

    def test_a_table_with_no_fold_change_column_has_no_splits(self):
        measured = measure_table(b"sample\tcondition\nS1\tctrl\nS2\tko\n", thresholds=[2.0])
        assert measured.get("threshold_splits") in (None, {})


class TestCategoricalColumnsAreCounted:
    def test_a_low_cardinality_column_is_counted_by_value(self):
        """Groff's 146 sex-linked genes need the chromosome column read. This is generic: any
        column with few distinct values gets counted, whatever the paper is about."""
        measured = measure_table(_RESULTS, thresholds=[])
        assert measured["category_counts"]["chr"] == {"chr1": 1, "chrX": 2, "chrY": 1}

    def test_a_high_cardinality_column_is_not_counted(self):
        """A gene identifier column has one value per row. Counting it would store the table."""
        rows = "gene\tvalue\n" + "\n".join(f"GENE{i}\t{i}" for i in range(60))
        measured = measure_table(rows.encode(), thresholds=[])
        assert "gene" not in (measured.get("category_counts") or {})

    def test_a_numeric_column_is_not_counted_as_a_category(self):
        measured = measure_table(_RESULTS, thresholds=[])
        assert "baseMean" not in (measured.get("category_counts") or {})


class TestTheBasics:
    def test_rows_and_columns_are_reported(self):
        measured = measure_table(_RESULTS, thresholds=[])
        assert measured["row_count"] == 4
        assert "log2FoldChange" in measured["columns"]

    def test_the_rows_themselves_are_never_kept(self):
        measured = measure_table(_RESULTS, thresholds=[2.0])
        assert "GENE1" not in str(measured)

    def test_an_unreadable_blob_measures_nothing(self):
        assert measure_table(b"\x00\x01\x02", thresholds=[2.0]) == {}
