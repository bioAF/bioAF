"""plan_8_3 section 1.2 and section 0.1: the subset operation is REACHED on the paper it was written for.

The operation passed its own tests and never ran on Groff. `list_evidence` required the claim's count
and the table's citation in one sentence; the paper states 194 in the sentence that cites Supplemental
File 3, states the refinement in the sentence after it, and states 88 in the sentence after that. So
the parent was established, the refinement was never reached, and study 55's 88-gene claim came back
"the claim states no significance cutoff, and bioAF supplies none".

Two things are checked here, on the passage the live run actually recorded:

- the refinement is linked to the parent list it refines, bounded to the passage that cites the table,
  with the refinement's own sentences as its evidence and the parent's count as the list it must match;
- what that reaches is an UNRESOLVED magnitude, not credit, and the control that resolves it (a recorded
  filter-semantics confirmation) exists and reaches the operation.
"""

import pathlib

import pytest

from app.services.validation_author_consistency import check_claim
from app.services.validation_published_subset import SUBSET_COUNT
from app.services.validation_table_binding import list_evidence

_GROFF = pathlib.Path(__file__).parent / "fixtures" / "groff"

# The passage the live run recorded as the binding's evidence, verbatim.
_PASSAGE = (
    "Finally, we performed differential gene expression analyses between WEs with XX and XY karyotypes "
    "using the sex chromosome calls ascertained above. We identified 194 significantly differentially "
    "expressed genes of which 146 are sex-linked (Supplemental Fig. S2C; Supplemental Files S2, S3). We "
    "further refined this list by selecting those with a log2 fold change >2 and visualized their "
    "log2-transformed expression values in a heatmap (Supplemental Fig. S2D). Because the column "
    "dendrogram separates XX from XY WEs as in Figure 2A, these 88 genes may, in aggregate, constitute a "
    "suitable sexing gene list, similar to the approach taken above. We include the results of this "
    "analysis as Supplemental File S3."
)

_TABLE = {
    "name": "supp_gr.252981.119_Supplemental_File_3_XX-v-XY_siggenes.txt",
    "labels": ["Supplemental File S3"],
    "passages": [{"text": _PASSAGE, "source": "paper_text"}],
}


def _predicate(value):
    """The predicate study 55 recorded for each of the two claims on this table."""
    return {
        "status": "not_checkable",
        "reason": "the claim states no significance cutoff, and bioAF supplies none",
        "significance": None,
        "effect": {"kind": "abs_log2fc", "value": 2.0, "operator": ">"} if value == 88 else None,
        "count": {"value": value, "relation": "="},
    }


def _table_text() -> str:
    return (_GROFF / "supplemental_file_3_siggenes.txt").read_text(encoding="utf-8")


class TestTheParentClaimStillFindsItsOwnSentence:
    def test_the_194_claim_is_established_by_the_sentence_that_cites_the_table(self):
        found = list_evidence(_TABLE, _predicate(194))
        assert found["text"].startswith("We identified 194 significantly differentially expressed genes")
        assert found.get("refinement") is None


class TestTheRefinementIsLinkedToTheListItRefines:
    def test_the_88_claim_reaches_its_evidence_across_the_passages_sentences(self):
        found = list_evidence(_TABLE, _predicate(88))
        assert found is not None
        assert "further refined this list" in found["text"]
        assert "these 88 genes" in found["text"]

    def test_the_link_names_the_parent_it_refines(self):
        found = list_evidence(_TABLE, _predicate(88))
        assert "We identified 194" in found["refinement"]["parent_text"]
        assert 194 in found["refinement"]["parent_counts"]

    def test_the_refinements_sentence_need_not_cite_the_table(self):
        """The plan's rule: bound the link to the passage that cites the table, not to the sentence."""
        found = list_evidence(_TABLE, _predicate(88))
        assert "Supplemental File" not in found["text"]

    def test_a_count_stated_with_no_refinement_between_them_is_not_linked(self):
        """Two numbers in one passage are not a parent and a subset. Something has to say it refines."""
        table = {
            **_TABLE,
            "passages": [
                {
                    "text": (
                        "We identified 194 significantly differentially expressed genes (Supplemental File S3). "
                        "A separate analysis of the trophectoderm identified 88 genes."
                    ),
                    "source": "paper_text",
                }
            ],
        }
        assert list_evidence(table, _predicate(88)) is None

    def test_a_refinement_of_another_passages_list_is_not_linked(self):
        table = {
            **_TABLE,
            "passages": [{"text": "We further refined this list; these 88 genes remain.", "source": "s"}],
        }
        assert list_evidence(table, _predicate(88)) is None


class TestWhatItReachesIsUnresolvedAndNotCredit:
    """The refinement's wording is SIGNED. An absolute filter over this table happens to produce 88,
    and that is a plausible interpretation, never proof of the intended one."""

    def _check(self, confirmation=None):
        return check_claim(
            {},
            _predicate(88),
            {
                "name": _TABLE["name"],
                "text": _table_text(),
                "source": "supplement",
                "checksum": "8dc9a1878992f619f29fe635101914d27d79d23e0ffa86432c2c41a465e337d0",
                "confirmation": confirmation,
            },
            list_evidence=list_evidence(_TABLE, _predicate(88)),
        )

    def test_the_operation_is_reached(self):
        assert self._check()["method"] == SUBSET_COUNT

    def test_and_stops_on_the_magnitude_the_paper_does_not_state(self):
        record = self._check()
        assert record["outcome"] == "unresolved"
        assert "magnitude" in record["reason"]
        assert record["rows_passing"] is None

    def test_the_parent_it_counted_against_is_the_table_the_passage_names(self):
        record = self._check()
        assert record["subset"]["parent"]["count"] == 194
        assert record["subset"]["parent"]["verified"] is True

    def test_a_parent_whose_stated_count_is_not_this_table_is_refused(self):
        table = {
            **_TABLE,
            "passages": [
                {
                    "text": (
                        "We identified 30 significantly differentially expressed genes (Supplemental File S3). "
                        "We further refined this list by a log2 fold change >2; these 88 genes remain."
                    ),
                    "source": "paper_text",
                }
            ],
        }
        record = check_claim(
            {},
            _predicate(88),
            {"name": _TABLE["name"], "text": _table_text(), "source": "supplement"},
            list_evidence=list_evidence(table, _predicate(88)),
        )
        assert record["outcome"] == "unresolved"
        assert "not the same list" in record["reason"]


class TestARecordedFilterSemanticsResolvesIt:
    """The control section 0.4 requires for the unresolved magnitude: a person states which reading the
    paper meant, with the evidence, and nothing else about the filter."""

    def _check(self, semantics):
        return check_claim(
            {},
            _predicate(88),
            {
                "name": _TABLE["name"],
                "text": _table_text(),
                "source": "supplement",
                "confirmation": {"filter_semantics": semantics} if semantics else None,
            },
            list_evidence=list_evidence(_TABLE, _predicate(88)),
        )

    def test_a_magnitude_reading_settles_the_filter_and_the_count_follows(self):
        record = self._check({"magnitude": True, "note": "the authors' code takes abs(log2FoldChange)"})
        assert record["subset"]["filter"]["magnitude"] is True
        assert record["subset"]["filter"]["resolved_by"] == "confirmation"
        assert record["outcome"] in ("agree", "disagree")
        assert record["rows_passing"] == 88

    def test_a_signed_reading_settles_it_the_other_way(self):
        record = self._check({"magnitude": False, "note": "the legend plots only the up-regulated genes"})
        assert record["subset"]["filter"]["magnitude"] is False
        assert record["rows_passing"] == 26

    def test_a_confirmation_that_says_nothing_about_magnitude_resolves_nothing(self):
        record = self._check({"note": "it is the right table"})
        assert record["outcome"] == "unresolved"
        assert "magnitude" in record["reason"]


class TestTheConfirmationRecordsFilterSemantics:
    def test_an_entry_carries_the_reading_and_the_evidence(self):
        from app.services.validation_table_confirmations import confirmation_entry

        entry = confirmation_entry(
            table="t",
            contrast="c",
            filter_semantics={"magnitude": True},
            note="the authors' code takes the absolute value",
            confirmed_by="a person",
            at="2026-09-17T00:00:00+00:00",
        )
        assert entry["filter_semantics"] == {"magnitude": True}

    def test_a_reading_that_is_not_one_of_the_two_is_refused(self):
        from app.services.validation_table_confirmations import ConfirmationRefused, confirmation_entry

        with pytest.raises(ConfirmationRefused, match="magnitude"):
            confirmation_entry(
                table="t",
                contrast="c",
                filter_semantics={"magnitude": "absolute"},
                note="n",
                confirmed_by="a person",
                at="2026-09-17T00:00:00+00:00",
            )

    def test_it_reaches_the_operation_through_the_production_check(self):
        """The gap section 0.1 is about: the operation read `table["confirmation"]["filter_semantics"]`
        and the production caller never put a confirmation on the table it passed."""
        from app.services.validation_consistency_checks import CHECK_TABLE_KEYS

        assert "confirmation" in CHECK_TABLE_KEYS
