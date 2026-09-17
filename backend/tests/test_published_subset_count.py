"""plan_8_3 section 1.2: a documented refinement of a published parent list, counted as its own check.

A complete published list can establish its total while a required refinement of it stays unassessed.
Groff's Supplemental File 3 holds 194 rows, bioAF already agrees with the paper's 194, and the
paper's other claim, that 88 of them clear a fold change of 2, had no operation behind it.

The operation is explicit: a verified parent selected set, a source-backed filter, the subset, and a
count of distinct entities. Its semantics come from evidence, never from arithmetic: the paper's
literal wording, "log2 fold change > 2", is SIGNED, and a calculation that happens to produce the
paper's 88 with an absolute filter is a plausible interpretation, not proof that it is the intended
one. Where the evidence does not resolve magnitude from direction, the check stays unresolved with
that reason, and a recorded source-backed clarification is what settles it.
"""

import pytest

from app.services.validation_published_subset import (
    SUBSET_COUNT,
    SUBSET_VERSION,
    filter_from_statement,
    subset_count,
)

_HEADER = "gene\tchromosome\tlog2FoldChange\tpadj"


def _rows(values) -> str:
    return "\n".join([_HEADER, *(f"{g}\t{c}\t{lfc}\t{p}" for g, c, lfc, p in values)]) + "\n"


# Eight genes: three above +2, two below -2, three between. Signed > 2 counts 3; absolute counts 5.
_TABLE = _rows(
    [
        ("A", "chr1", "3.1", "0.001"),
        ("B", "chr1", "2.4", "0.002"),
        ("C", "chrX", "2.2", "0.003"),
        ("D", "chrY", "-2.6", "0.004"),
        ("E", "chr2", "-3.9", "0.005"),
        ("F", "chr3", "1.1", "0.006"),
        ("G", "chr4", "-0.4", "0.007"),
        ("H", "chr5", "0.9", "0.008"),
    ]
)

_PARENT = {"verified": True, "count": 8, "source": "Supplemental File 3", "checksum": "abc"}


class TestReadingTheFilterFromWhatThePaperSays:
    def test_a_signed_statement_is_read_as_signed_and_says_it_is_unresolved(self):
        found = filter_from_statement("194 genes, 88 of which had a log2 fold change > 2")
        assert found["operator"] == ">"
        assert found["value"] == 2.0
        assert found["scale"] == "log2"
        assert found["magnitude"] is None
        assert "magnitude" in found["unresolved"]

    def test_a_statement_that_says_absolute_is_resolved(self):
        found = filter_from_statement("genes with an absolute log2 fold change > 2")
        assert found["magnitude"] is True
        assert found["unresolved"] is None

    def test_a_statement_naming_both_directions_is_resolved_as_magnitude(self):
        found = filter_from_statement("genes up or down by more than 2-fold on a log2 scale")
        assert found["magnitude"] is True

    def test_a_statement_naming_one_direction_is_resolved_as_signed(self):
        found = filter_from_statement("genes upregulated with a log2 fold change greater than 2")
        assert found["magnitude"] is False
        assert found["direction"] == "up"
        assert found["unresolved"] is None

    def test_an_inclusive_boundary_is_read_as_inclusive(self):
        assert filter_from_statement("a log2 fold change of at least 2")["operator"] == ">="

    def test_a_statement_with_no_cutoff_resolves_to_nothing(self):
        assert filter_from_statement("the genes we found most interesting") is None


class TestTheOperation:
    def test_an_unresolved_filter_never_produces_a_count(self):
        result = subset_count(_TABLE, parent=_PARENT, statement="88 of which had a log2 fold change > 2", claimed=3)
        assert result["status"] == "unresolved"
        assert "magnitude" in result["reason"]
        assert result["count"] is None

    def test_a_resolved_magnitude_filter_counts_distinct_entities(self):
        result = subset_count(_TABLE, parent=_PARENT, statement="an absolute log2 fold change > 2", claimed=5)
        assert result["status"] == "agree"
        assert result["count"] == 5

    def test_a_resolved_signed_filter_counts_only_that_direction(self):
        result = subset_count(_TABLE, parent=_PARENT, statement="upregulated with a log2 fold change > 2", claimed=3)
        assert result["status"] == "agree"
        assert result["count"] == 3

    def test_a_count_that_differs_is_a_disagreement_not_a_failure(self):
        result = subset_count(_TABLE, parent=_PARENT, statement="an absolute log2 fold change > 2", claimed=88)
        assert result["status"] == "disagree"
        assert result["count"] == 5

    def test_it_records_the_operation_its_version_and_the_filter_it_applied(self):
        result = subset_count(_TABLE, parent=_PARENT, statement="an absolute log2 fold change > 2", claimed=5)
        assert result["method"] == SUBSET_COUNT
        assert result["version"] == SUBSET_VERSION
        assert result["filter"]["magnitude"] is True
        assert result["parent"]["source"] == "Supplemental File 3"
        assert result["parent"]["count"] == 8


class TestTheParentMustBeTheCompletePublishedList:
    def test_an_unverified_parent_refuses_the_subset(self):
        result = subset_count(
            _TABLE,
            parent={**_PARENT, "verified": False, "reason": "the passage calls it a selection"},
            statement="an absolute log2 fold change > 2",
            claimed=5,
        )
        assert result["status"] == "unresolved"
        assert "complete published" in result["reason"]

    def test_a_parent_whose_row_count_does_not_match_the_table_refuses(self):
        result = subset_count(
            _TABLE, parent={**_PARENT, "count": 194}, statement="an absolute log2 fold change > 2", claimed=5
        )
        assert result["status"] == "unresolved"
        assert "194" in result["reason"]

    def test_counting_the_published_list_does_not_need_the_original_selection_threshold(self):
        """A published list is already selected; reapplying an unavailable cutoff is not required."""
        result = subset_count(_TABLE, parent=_PARENT, statement="an absolute log2 fold change > 2", claimed=5)
        assert result["status"] == "agree"


class TestWhatTheTableMustCarry:
    def test_a_filter_on_a_column_the_table_does_not_hold_is_unresolved(self):
        table = "gene\tpadj\nA\t0.001\nB\t0.002\n"
        result = subset_count(
            table, parent={**_PARENT, "count": 2}, statement="an absolute log2 fold change > 2", claimed=1
        )
        assert result["status"] == "unresolved"
        assert "fold-change column" in result["reason"]

    def test_a_fold_change_only_refinement_does_not_need_a_p_value_column(self):
        table = "gene\tlog2FoldChange\nA\t3.1\nB\t0.2\n"
        result = subset_count(
            table, parent={**_PARENT, "count": 2}, statement="an absolute log2 fold change > 2", claimed=1
        )
        assert result["status"] == "agree"

    def test_a_refinement_that_needs_a_p_value_the_table_lacks_is_blocked(self):
        table = "gene\tlog2FoldChange\nA\t3.1\nB\t0.2\n"
        result = subset_count(
            table, parent={**_PARENT, "count": 2}, statement="significant at adjusted P < 0.01", claimed=1
        )
        assert result["status"] == "unresolved"
        assert "adjusted P" in result["reason"]

    def test_duplicate_identifiers_are_counted_once_and_reported(self):
        table = _rows([("A", "chr1", "3.1", "0.001"), ("A", "chr1", "3.1", "0.001"), ("B", "chr1", "2.5", "0.002")])
        result = subset_count(
            table, parent={**_PARENT, "count": 3}, statement="an absolute log2 fold change > 2", claimed=2
        )
        assert result["count"] == 2
        assert result["duplicates"] == 1

    def test_rows_with_no_identifier_are_excluded_and_reported(self):
        table = _rows([("A", "chr1", "3.1", "0.001"), ("", "chr1", "2.5", "0.002")])
        result = subset_count(
            table, parent={**_PARENT, "count": 2}, statement="an absolute log2 fold change > 2", claimed=1
        )
        assert result["count"] == 1
        assert result["rows_without_identifier"] == 1

    def test_a_missing_value_in_the_filtered_column_is_excluded_and_reported(self):
        table = "gene\tlog2FoldChange\nA\t3.1\nB\tNA\n"
        result = subset_count(
            table, parent={**_PARENT, "count": 2}, statement="an absolute log2 fold change > 2", claimed=1
        )
        assert result["count"] == 1
        assert result["rows_without_value"] == 1


class TestARecordedClarificationSettlesTheSemantics:
    def test_a_confirmation_resolving_magnitude_lets_the_count_run(self):
        result = subset_count(
            _TABLE,
            parent=_PARENT,
            statement="88 of which had a log2 fold change > 2",
            claimed=5,
            confirmation={"magnitude": True, "note": "Figure 4's legend plots |log2FC|", "confirmed_by": "a@b.c"},
        )
        assert result["status"] == "agree"
        assert result["count"] == 5
        assert result["filter"]["resolved_by"] == "confirmation"
        assert result["filter"]["note"] == "Figure 4's legend plots |log2FC|"

    def test_a_confirmation_that_says_nothing_about_the_semantics_does_not_resolve_them(self):
        result = subset_count(
            _TABLE,
            parent=_PARENT,
            statement="88 of which had a log2 fold change > 2",
            claimed=5,
            confirmation={"note": "it is the right table"},
        )
        assert result["status"] == "unresolved"

    @pytest.mark.parametrize("claimed", [3, 5])
    def test_the_count_is_never_chosen_to_match_the_claim(self, claimed):
        """Both readings are computable; only evidence picks one, and it is picked before counting."""
        result = subset_count(
            _TABLE, parent=_PARENT, statement="88 of which had a log2 fold change > 2", claimed=claimed
        )
        assert result["status"] == "unresolved"


class TestItRunsThroughTheConsistencyCheck:
    """The dispatch: a claim refining a published list takes the subset operation, not the list count."""

    @staticmethod
    def _check(statement, claimed, effect=True):
        from app.services.validation_author_consistency import check_claim

        predicate = {
            "status": "not_checkable",
            "reason": "the claim states no significance cutoff",
            "significance": None,
            "effect": {"kind": "abs_log2fc", "value": 2.0, "operator": ">"} if effect else None,
            "count": {"value": claimed, "relation": "="},
            "stated_as": statement,
        }
        return check_claim(
            {"claim_text": statement},
            predicate,
            {"name": "Supplemental File 3", "text": _TABLE, "checksum": "abc"},
            list_evidence={"text": statement, "source": "paper_text"},
        )

    def test_a_refining_claim_takes_the_subset_operation(self):
        record = self._check("an absolute log2 fold change > 2", 5)
        assert record["method"] == SUBSET_COUNT
        assert record["outcome"] == "agree"
        assert record["subset"]["count"] == 5

    def test_a_whole_list_claim_still_takes_the_list_count(self):
        from app.services.validation_author_consistency import LIST_COUNT

        record = self._check("194 genes were differentially expressed", 8, effect=False)
        assert record["method"] == LIST_COUNT

    def test_an_unresolved_refinement_is_unresolved_with_its_reason_and_no_count(self):
        record = self._check("88 of which had a log2 fold change > 2", 5)
        assert record["outcome"] == "unresolved"
        assert "magnitude" in record["reason"]
        assert record["rows_passing"] is None

    def test_the_record_carries_the_operation_its_parent_and_its_filter(self):
        record = self._check("an absolute log2 fold change > 2", 5)
        assert record["subset"]["parent"]["source"] == "Supplemental File 3"
        assert record["subset"]["parent"]["checksum"] == "abc"
        assert record["subset"]["filter"]["magnitude"] is True
        assert record["subset"]["version"] == SUBSET_VERSION
