"""plan_8_3 stage 5: a unit's TYPE is not its identity, and neither is a repository accession.

Study 50's prompt asks for "the independent unit (animal, donor, independently derived clone,
culture)". The model answered `gingival fibroblast culture` for all 21 columns, which is what kind of
thing each one is. `design_from_mapping` read that string as a unique identity, concluded that six
columns measured one biological sample, and rejected both arms as unsupported technical replication.
Had it been allowed through it would have corrupted the pairing instead.

Type, unit identity, biological sample, condition and time point are separate facts, and each
relationship a design rests on needs its own evidence. A unit identity bioAF cannot establish is
unresolved biological identity: it is not missing experimental data, and it is not a discrepancy in
the paper.
"""

import json
import pathlib

import pytest

from app.services.validation_input_choice import (
    UNIT_TYPE_WORDS,
    design_from_mapping,
    unit_identity,
    validate_mapping,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "panahipour"


def _records() -> list[dict]:
    return json.loads((_FIXTURE / "sample_records.json").read_text(encoding="utf-8"))


def _saved_proposal() -> list[dict]:
    """The proposal study 50 actually saved: one generic unit type for every column."""
    from app.services.validation_input_choice import record_reference

    rows = []
    for record in _records():
        condition = record["condition"]
        arm = (
            "test"
            if condition.endswith("Mucoderm® + HA")
            else "reference"
            if condition.endswith("treated with Mucoderm®")
            else "excluded"
        )
        rows.append(
            {
                "column": record["title"],
                "arm": arm,
                "biological_unit": "gingival fibroblast culture",
                "biological_sample": f"gingival fibroblast culture {condition}",
                "technical_group": None,
                "time_point": None,
                "evidence": [{"source": "sample_record", "quote": record_reference(record)}],
            }
        )
    return rows


def _contrast() -> dict:
    return {
        "name": "membrane with HA versus membrane",
        "test_condition": "Mucoderm® + HA",
        "reference_condition": "Mucoderm®",
    }


class TestAGenericUnitTypeIsNotAnIdentity:
    def test_the_saved_proposal_fails_on_identity_not_on_technical_replication(self):
        records = _records()
        result = validate_mapping(
            _saved_proposal(),
            columns=[r["title"] for r in records],
            sample_records=records,
            contrast=_contrast(),
        )
        assert result["status"] == "unresolved"
        assert any("gingival fibroblast culture" in r and "kind of unit" in r for r in result["reasons"])
        assert not any("technical replicate" in r for r in result["reasons"])

    def test_the_design_is_never_built_from_one_and_says_which_fact_is_missing(self):
        design = {"contrasts": [{**_contrast(), "test_samples": [], "reference_samples": []}]}
        rewritten, status, reason = design_from_mapping(design, _saved_proposal(), contrast_index=0)
        assert status == "unresolved_identity"
        assert "which culture" in reason or "identity" in reason
        assert "technical replicates" not in reason

    @pytest.mark.parametrize("word", sorted(UNIT_TYPE_WORDS))
    def test_every_word_the_prompt_offers_as_an_example_is_a_type_not_an_identity(self, word):
        assert unit_identity(word)["kind"] == "type"

    def test_a_type_qualified_by_a_condition_is_still_not_an_identity(self):
        assert unit_identity("gingival fibroblast culture treated with Mucoderm")["kind"] == "type"

    def test_a_named_donor_is_an_identity(self):
        assert unit_identity("donor 3")["kind"] == "identity"
        assert unit_identity("GSM9259432")["kind"] == "identity"


class TestWhatAnAccessionEstablishes:
    def test_a_sample_accession_is_repository_identity_not_donor_identity(self):
        records = _records()
        mapping = [{**row, "biological_unit": _accession_for(row["column"], records)} for row in _saved_proposal()]
        result = validate_mapping(
            mapping, columns=[r["title"] for r in records], sample_records=records, contrast=_contrast()
        )
        assert result["status"] == "accepted"
        design = {"contrasts": [{**_contrast(), "test_samples": [], "reference_samples": []}]}
        rewritten, status, reason = design_from_mapping(design, mapping, contrast_index=0)
        assert status == "ok"
        contrast = rewritten["contrasts"][0]
        assert len(contrast["test_samples"]) == 3
        assert len(contrast["reference_samples"]) == 3
        # Three distinct repository samples per arm, and NOT three established donors.
        assert contrast.get("subjects") is None
        assert contrast["unit_basis"] == "repository_sample"

    def test_it_does_not_become_a_pairing(self):
        """The paper reports three independent donors. No record names one, so nothing pairs."""
        records = _records()
        mapping = [{**row, "biological_unit": _accession_for(row["column"], records)} for row in _saved_proposal()]
        design = {
            "contrasts": [
                {**_contrast(), "test_samples": [], "reference_samples": [], "subjects": {"a": "donor 1"}}
            ]
        }
        rewritten, status, reason = design_from_mapping(design, mapping, contrast_index=0)
        assert status == "pairing_lost"
        assert "paired" in reason


def _accession_for(column: str, records: list[dict]) -> str:
    return next(r["geo_accession"] for r in records if r["title"] == column)


class TestDonorIdentityIsNeverInferred:
    def test_a_repeating_number_pattern_across_the_arms_is_not_a_donor(self):
        records = [
            {"title": f"LP{i:03d}", "geo_accession": f"GSM{i}", "condition": f"treatment: {t}"}
            for i, t in ((1, "a"), (2, "b"), (11, "a"), (12, "b"))
        ]
        mapping = [
            {
                "column": r["title"],
                "arm": "test" if r["condition"].endswith("a") else "reference",
                # The LP numbering suggests two donors; nothing in the records says so.
                "biological_unit": "donor " + r["title"][-1],
                "biological_sample": r["title"],
                "evidence": [{"source": "column_name", "quote": r["title"]}],
            }
            for r in records
        ]
        result = validate_mapping(mapping, columns=[r["title"] for r in records], sample_records=records)
        assert result["status"] == "unresolved"
        assert any("does not state" in r for r in result["reasons"])

    def test_a_donor_the_records_do_state_is_accepted(self):
        records = [
            {"title": "S1", "geo_accession": "GSM1", "condition": "donor: D1; treatment: a"},
            {"title": "S2", "geo_accession": "GSM2", "condition": "donor: D2; treatment: a"},
            {"title": "S3", "geo_accession": "GSM3", "condition": "donor: D1; treatment: b"},
            {"title": "S4", "geo_accession": "GSM4", "condition": "donor: D2; treatment: b"},
        ]
        from app.services.validation_input_choice import record_reference

        mapping = [
            {
                "column": r["title"],
                "arm": "test" if r["condition"].endswith("a") else "reference",
                "biological_unit": r["condition"].split(";")[0].split(":")[1].strip(),
                "biological_sample": f"{r['title']} {r['condition']}",
                "evidence": [{"source": "sample_record", "quote": record_reference(r)}],
            }
            for r in records
        ]
        result = validate_mapping(mapping, columns=[r["title"] for r in records], sample_records=records)
        assert result["reasons"] == []

    def test_a_paired_design_keeps_its_three_subjects_across_both_arms(self):
        records = [
            {"title": f"S{i}", "geo_accession": f"GSM{i}", "condition": f"donor: D{d}; treatment: {t}"}
            for i, d, t in ((1, 1, "a"), (2, 2, "a"), (3, 3, "a"), (4, 1, "b"), (5, 2, "b"), (6, 3, "b"))
        ]
        from app.services.validation_input_choice import record_reference

        mapping = [
            {
                "column": r["title"],
                "arm": "test" if r["condition"].endswith("a") else "reference",
                "biological_unit": f"D{r['condition'].split('donor: D')[1][0]}",
                "biological_sample": f"{r['title']}",
                "evidence": [{"source": "sample_record", "quote": record_reference(r)}],
            }
            for r in records
        ]
        design = {
            "contrasts": [
                {
                    "name": "a vs b",
                    "test_condition": "a",
                    "reference_condition": "b",
                    "test_samples": [],
                    "reference_samples": [],
                    "subjects": {"x": "D1"},
                }
            ]
        }
        rewritten, status, reason = design_from_mapping(design, mapping, contrast_index=0)
        assert status == "ok", reason
        subjects = rewritten["contrasts"][0]["subjects"]
        assert sorted(set(subjects.values())) == ["D1", "D2", "D3"]
        assert len(subjects) == 6


class TestARecordedConfirmationCanSupplyWhatThePaperDoesNot:
    def test_a_confirmed_unit_identity_is_accepted_and_labelled_assisted(self):
        records = _records()
        titles = [r["title"] for r in records]
        confirmed = {t: f"donor {i % 3 + 1}" for i, t in enumerate(titles)}
        mapping = [
            {
                **row,
                "biological_unit": confirmed[row["column"]],
                "evidence": [
                    *row["evidence"],
                    {
                        "source": "confirmation",
                        "quote": f"{row['column']} was taken from {confirmed[row['column']]}",
                        "confirmed_by": "someone@example.com",
                    },
                ],
            }
            for row in _saved_proposal()
        ]
        result = validate_mapping(
            mapping, columns=titles, sample_records=records, contrast=_contrast(), confirmed_units=confirmed
        )
        assert result["reasons"] == []
        assert result["assistance"] == "unit_identity_confirmed"

    def test_without_the_confirmation_the_same_mapping_is_unresolved(self):
        records = _records()
        titles = [r["title"] for r in records]
        confirmed = {t: f"donor {i % 3 + 1}" for i, t in enumerate(titles)}
        mapping = [{**row, "biological_unit": confirmed[row["column"]]} for row in _saved_proposal()]
        result = validate_mapping(mapping, columns=titles, sample_records=records, contrast=_contrast())
        assert result["status"] == "unresolved"
        assert any("does not state" in r for r in result["reasons"])
