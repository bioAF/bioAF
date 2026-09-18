"""plan_8_3 section 3.1: an arm's condition is compared to a record's ATTRIBUTES, not to its prose.

The treatment-compatibility check decided scientific compatibility with a substring test. A paper
writes a condition as prose ("SAMD1 KO mouse ES cells"); a repository writes the same fact as
attributes ("antibody: none; genotype: SAMD1KO; cell line: E14TG2a"). The phrase is absent from the
record that states exactly what it says, so the check refused every row of study 56's correct
mapping, the reference arm as well as the test arm, and it would do so on any paper whose contrast
names are prose.

Reading the record as the attributes it states repairs the WORDING comparison and nothing else. Every
refusal stays: a value that contradicts the arm, a record describing another sample, and an arm bioAF
cannot place against any attribute the deposit states are three different answers.
"""

import json
import pathlib

import pytest

from app.services.sample_record_attributes import (
    COMPATIBLE,
    CONTRADICTED,
    NOT_STATED,
    UNRESOLVED,
    attributes,
    condition_match,
)

_PANAHIPOUR = pathlib.Path(__file__).parent / "fixtures" / "panahipour"

# Study 56's records as the read recorded them: the same fact as the paper's prose, written as
# attributes. The GEO characteristics of a mouse ES cell knockout series.
_SAMD1 = [
    {
        "geo_accession": "GSM4287335",
        "title": "RNA-Seq WT repl1",
        "condition": "antibody: none; genotype: WT; cell line: E14TG2a",
    },
    {
        "geo_accession": "GSM4287339",
        "title": "RNA-Seq SAMD1KO repl1",
        "condition": "antibody: none; genotype: SAMD1KO; cell line: E14TG2a",
    },
]


def _panahipour_records() -> list[dict]:
    return json.loads((_PANAHIPOUR / "sample_records.json").read_text(encoding="utf-8"))


class TestARecordIsReadAsTheAttributesItStates:
    def test_each_characteristic_becomes_a_named_attribute(self):
        stated = {a.name: a.value for a in attributes(_SAMD1[1])}
        assert stated == {"antibody": "none", "genotype": "SAMD1KO", "cell line": "E14TG2a"}

    def test_a_characteristic_with_no_name_keeps_its_value(self):
        stated = attributes({"condition": "gingival fibroblast"})
        assert [(a.name, a.value) for a in stated] == [(None, "gingival fibroblast")]

    def test_a_record_that_states_nothing_has_no_attributes(self):
        assert attributes({"condition": ""}) == ()


class TestAnArmWrittenAsProseMatchesTheAttributeThatStatesIt:
    """The study-56 regression. Neither arm's phrase occurs in any record, and both are correct."""

    @pytest.mark.parametrize(
        "arm, record",
        [("WT mouse ES cells", _SAMD1[0]), ("SAMD1 KO mouse ES cells", _SAMD1[1])],
    )
    def test_the_genotype_the_arm_names_is_the_genotype_the_record_states(self, arm, record):
        found = condition_match(arm, record, _SAMD1)
        assert found["status"] == COMPATIBLE
        assert found["attribute"] == "genotype"

    def test_a_spelling_difference_within_one_value_is_not_a_disagreement(self):
        """ "SAMD1 KO" and "SAMD1KO" are the same genotype written two ways."""
        assert condition_match("SAMD1KO mouse ES cells", _SAMD1[1], _SAMD1)["status"] == COMPATIBLE

    def test_the_wrong_genotype_in_that_arm_is_still_refused(self):
        found = condition_match("SAMD1 KO mouse ES cells", _SAMD1[0], _SAMD1)
        assert found["status"] == CONTRADICTED
        assert found["attribute"] == "genotype"
        assert found["stated"] == "WT"


class TestAConditionStatedAsATreatmentStillHoldsItsArmsApart:
    """Study 50's contrast. The arms differ by one word of the same attribute's value, and the
    attribute reading must not make the narrower arm accept the wider one's samples."""

    def test_the_membrane_with_ha_arm_takes_the_records_that_state_it(self):
        record = next(r for r in _panahipour_records() if r["condition"].endswith("Mucoderm® + HA"))
        found = condition_match("Mucoderm® + HA", record, _panahipour_records())
        assert found["status"] == COMPATIBLE
        assert found["attribute"] == "treatment"

    def test_the_membrane_arm_does_not_take_a_membrane_with_ha_record(self):
        record = next(r for r in _panahipour_records() if r["condition"].endswith("Mucoderm® + HA"))
        assert condition_match("Mucoderm®", record, _panahipour_records())["status"] == CONTRADICTED

    def test_an_abbreviation_the_value_itself_states_is_the_same_value(self):
        """ "treatment: Tissue culture surface (TCS)" states TCS, so the arm "TCS" is that value."""
        record = next(r for r in _panahipour_records() if "(TCS)" in r["condition"])
        found = condition_match("TCS", record, _panahipour_records())
        assert found["status"] == COMPATIBLE
        assert found["attribute"] == "treatment"

    def test_a_unicode_symbol_in_the_value_produces_no_false_failure(self):
        record = next(r for r in _panahipour_records() if r["condition"].endswith("Collafleece®"))
        assert condition_match("Collafleece", record, _panahipour_records())["status"] == COMPATIBLE


class TestAParenthesisIsAnAlternativeSpellingOnlyWhenItIsOne:
    """Study 56's deposit is a COMBINED series: its ChIP records state `antibody: SAMD1 (self-made)`.
    Reading that parenthesis as an alternative spelling put the arm `SAMD1 KO mouse ES cells` against
    the antibody, and every RNA-seq record states `antibody: none`, so the whole mapping was refused
    a second time for a reason that had nothing to do with it."""

    def test_an_antibody_whose_name_begins_with_a_gene_is_not_the_genotype(self):
        chip = {
            "geo_accession": "GSM4287309",
            "title": "ChIP WT",
            "condition": "antibody: SAMD1 (self-made); genotype: WT",
        }
        found = condition_match("SAMD1 KO mouse ES cells", _SAMD1[1], [chip, *_SAMD1])
        assert found["status"] == COMPATIBLE
        assert found["attribute"] == "genotype"

    def test_an_initialism_the_value_states_is_still_the_same_value(self):
        records = [{"geo_accession": "GSM1", "title": "a", "condition": "treatment: Tissue culture surface (TCS)"}]
        assert condition_match("TCS", records[0], records)["status"] == COMPATIBLE


class TestAValueWrittenInWordsIsAlsoWrittenAsItsInitials:
    """ "WT" and "genotype: wild type" are one genotype. The deployed rule accepted that row only by
    accident, finding "wt" inside the unrelated value "WT1" of another attribute."""

    def test_the_abbreviation_and_the_words_are_the_same_genotype(self):
        records = [
            {"geo_accession": "GSM1", "title": "a", "condition": "genotype: wild type; culture: WT1"},
            {"geo_accession": "GSM2", "title": "b", "condition": "genotype: SAMD1 KO; clone: c5"},
        ]
        found = condition_match("WT", records[0], records)
        assert found["status"] == COMPATIBLE
        assert found["attribute"] == "genotype"

    def test_the_other_arms_records_are_still_refused(self):
        records = [
            {"geo_accession": "GSM1", "title": "a", "condition": "genotype: wild type; culture: WT1"},
            {"geo_accession": "GSM2", "title": "b", "condition": "genotype: SAMD1 KO; clone: c5"},
        ]
        assert condition_match("WT", records[1], records)["status"] == CONTRADICTED
        assert condition_match("SAMD1 KO", records[0], records)["status"] == CONTRADICTED


class TestAnArmBioafCannotPlaceIsUnresolvedAndSaysWhatWasAvailable:
    def test_an_arm_no_attribute_states_is_unresolved(self):
        found = condition_match("hypoxia for 24 hours", _SAMD1[1], _SAMD1)
        assert found["status"] == UNRESOLVED
        assert found["available"] == ["antibody", "cell line", "genotype"]

    def test_a_record_that_does_not_state_the_attribute_the_arm_names_is_unresolved(self):
        """An absent attribute is never read as agreement."""
        silent = {"geo_accession": "GSM1", "title": "s1", "condition": "cell line: E14TG2a"}
        found = condition_match("SAMD1 KO mouse ES cells", silent, [*_SAMD1, silent])
        assert found["status"] == UNRESOLVED
        assert found["attribute"] == "genotype"

    def test_a_record_stating_no_attributes_at_all_is_unresolved(self):
        empty = {"geo_accession": "GSM2", "title": "s2", "condition": ""}
        assert condition_match("SAMD1 KO mouse ES cells", empty, [*_SAMD1, empty])["status"] == UNRESOLVED


class TestAComparisonWithNothingToReadDoesNotRefuse:
    """A refusal has to rest on evidence. Where there is none the arm assignment stands on whatever
    placed it, exactly as it did before this check existed."""

    def test_an_arm_the_contrast_leaves_blank_is_not_compared(self):
        assert condition_match("", _SAMD1[1], _SAMD1)["status"] == NOT_STATED

    def test_a_deposit_whose_records_state_nothing_is_not_compared(self):
        silent = [{"geo_accession": "GSM1", "title": "a", "condition": ""}]
        assert condition_match("SAMD1 KO mouse ES cells", silent[0], silent)["status"] == NOT_STATED

    def test_an_unnamed_characteristic_is_still_an_attribute_to_disagree_with(self):
        """A deposit that writes its characteristics without names still states facts."""
        records = [
            {"geo_accession": "GSM1", "title": "a", "condition": "Mucoderm"},
            {"geo_accession": "GSM2", "title": "b", "condition": "Collafleece"},
        ]
        assert condition_match("Mucoderm", records[0], records)["status"] == COMPATIBLE
        assert condition_match("Mucoderm", records[1], records)["status"] == CONTRADICTED


class TestEveryAttributeTheArmNamesHasToAgree:
    def test_an_arm_naming_two_attributes_needs_both(self):
        records = [
            {"geo_accession": "GSM1", "title": "a", "condition": "cell type: fibroblast; treatment: Mucoderm®"},
            {"geo_accession": "GSM2", "title": "b", "condition": "cell type: keratinocyte; treatment: Mucoderm®"},
        ]
        assert condition_match("fibroblast treated with Mucoderm®", records[0], records)["status"] == COMPATIBLE
        found = condition_match("fibroblast treated with Mucoderm®", records[1], records)
        assert found["status"] == CONTRADICTED
        assert found["attribute"] == "cell type"
