"""plan_8_3 stage 3: the validator sees the same evidence the proposer was given.

`build_input_prompt` printed each sample record as `accession | title | characteristics`. The model
quoted a record back exactly as it was shown, and `validate_mapping` searched a corpus built as
`accession title characteristics`: lowercasing and whitespace normalization do not remove the pipes.
All 21 of study 50's quotations were rejected for citing evidence that was, in fact, in front of it.

One canonical record representation now serves both, and a mapping evidence item identifies the record
it cites rather than being matched against a flattened corpus. Every scientific check stays: a quote
from the wrong sample, a changed treatment, a fabricated record and a contradictory condition are
each refused with their own reason.
"""

import json
import pathlib

import pytest

from app.services.validation_input_choice import (
    build_input_prompt,
    record_reference,
    source_records,
    validate_mapping,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "panahipour"


def _records() -> list[dict]:
    return json.loads((_FIXTURE / "sample_records.json").read_text(encoding="utf-8"))


def _contrast() -> dict:
    return {
        "name": "membrane with HA versus membrane",
        "test_condition": "Mucoderm® + HA",
        "reference_condition": "Mucoderm®",
    }


def _columns() -> list[str]:
    return [r["title"] for r in _records()]


def _mapping_of(records: list[dict], *, test: str, reference: str) -> list[dict]:
    """The mapping the model produced on study 50, quoting each record exactly as it was shown."""
    rows = []
    for record in records:
        condition = record["condition"]
        arm = "test" if condition.endswith(test) else "reference" if condition.endswith(reference) else "excluded"
        rows.append(
            {
                "column": record["title"],
                "arm": arm,
                "biological_unit": record["geo_accession"],
                "biological_sample": f"{record['geo_accession']} under {condition}",
                "technical_group": None,
                "time_point": None,
                "evidence": [{"source": "sample_record", "quote": record_reference(record)}],
            }
        )
    return rows


def _shown_records(prompt_payload: str) -> list[str]:
    """The record lines the prompt actually shows, as the model reads them."""
    block = prompt_payload.split("The repository's sample records")[1]
    return [ln.strip() for ln in block.splitlines() if ln.strip().startswith("GSM")]


class TestThePromptAndTheValidatorShareOneRepresentation:
    def test_a_quote_taken_verbatim_from_the_prompt_validates(self):
        """The regression: the quotations are read out of the prompt itself, not canned twice."""
        records = _records()
        _, payload = build_input_prompt(
            claim={"claim_text": "a claim"},
            predicate_words=None,
            contrast=_contrast(),
            experiment={"id": "e1", "assay": "bulk RNA-seq"},
            sample_records=records,
            previews=[{"filename": "m.tsv", "header": ["ENSG", *_columns()], "rows": []}],
            tables=[],
        )
        shown = _shown_records(payload)
        assert len(shown) == len(records)
        mapping = [
            {
                "column": record["title"],
                "arm": "test"
                if "+ HA" in record["condition"]
                else "reference"
                if record["condition"].endswith("Mucoderm®")
                else "excluded",
                "biological_unit": record["geo_accession"],
                "biological_sample": f"{record['geo_accession']} under {record['condition']}",
                "evidence": [{"source": "sample_record", "quote": line}],
            }
            for record, line in zip(records, shown, strict=True)
        ]
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert result["reasons"] == []
        assert result["status"] == "accepted"

    def test_the_records_shown_are_the_records_validated(self):
        records = _records()
        assert [record_reference(r) for r in records] == [r["reference"] for r in source_records(records)]


class TestWhatIsStillRefused:
    def test_a_fabricated_record_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        mapping[0]["evidence"] = [{"source": "sample_record", "quote": "GSM0000000 | LP99999 | treatment: invented"}]
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert result["status"] == "unresolved"
        assert any("no sample record" in r for r in result["reasons"])

    def test_a_changed_treatment_is_refused_even_though_the_record_exists(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        row = next(r for r in mapping if r["arm"] == "test")
        record = next(r for r in records if r["title"] == row["column"])
        row["evidence"] = [
            {
                "source": "sample_record",
                "quote": record_reference({**record, "condition": record["condition"] + " and something else"}),
            }
        ]
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert result["status"] == "unresolved"
        assert any("does not match" in r for r in result["reasons"])

    def test_a_valid_quote_from_the_wrong_sample_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        row = next(r for r in mapping if r["arm"] == "test")
        other = next(r for r in records if r["title"] != row["column"])
        row["evidence"] = [{"source": "sample_record", "quote": record_reference(other)}]
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert result["status"] == "unresolved"
        assert any("describes" in r and row["column"] in r for r in result["reasons"])

    def test_a_treatment_the_contrast_does_not_define_cannot_sit_in_an_arm(self):
        """The record is real and it is the column's own; it is the wrong arm for this contrast."""
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        excluded = next(r for r in mapping if r["arm"] == "excluded")
        excluded["arm"] = "test"
        result = validate_mapping(mapping, columns=_columns(), sample_records=records, contrast=_contrast())
        assert result["status"] == "unresolved"
        assert any("treatment" in r and excluded["column"] in r for r in result["reasons"])

    def test_an_arm_whose_records_hold_two_different_treatments_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        reference_rows = [r for r in mapping if r["arm"] == "reference"]
        odd = next(r for r in mapping if r["arm"] == "excluded")
        odd["arm"] = "reference"
        result = validate_mapping(mapping, columns=_columns(), sample_records=records, contrast=_contrast())
        assert result["status"] == "unresolved"
        assert reference_rows

    def test_a_column_the_matrix_does_not_hold_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        mapping[0]["column"] = "GeneSymbol"
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert any("does not hold" in r for r in result["reasons"])

    def test_a_column_assigned_twice_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        mapping.append(dict(mapping[0]))
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert any("more than once" in r for r in result["reasons"])

    def test_a_column_left_out_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")[:-1]
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert any("neither assigned" in r for r in result["reasons"])

    def test_a_row_citing_nothing_is_refused(self):
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        mapping[0]["evidence"] = []
        result = validate_mapping(mapping, columns=_columns(), sample_records=records)
        assert any("cites no evidence" in r for r in result["reasons"])


class TestFormattingIsNotEvidence:
    @pytest.mark.parametrize("rewrite", [lambda q: q.replace(" | ", " "), lambda q: q.replace(" | ", "\t"), str.strip])
    def test_a_quote_with_different_separators_is_the_same_citation(self, rewrite):
        """A presentation separator is not evidence. The record it names either exists or it does not."""
        records = _records()
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        for row in mapping:
            row["evidence"] = [{"source": "sample_record", "quote": rewrite(row["evidence"][0]["quote"])}]
        assert validate_mapping(mapping, columns=_columns(), sample_records=records)["status"] == "accepted"

    def test_a_unicode_treatment_name_is_preserved_and_matches(self):
        records = _records()
        assert any("®" in r["condition"] for r in records)
        mapping = _mapping_of(records, test="Mucoderm® + HA", reference="Mucoderm®")
        assert validate_mapping(mapping, columns=_columns(), sample_records=records)["status"] == "accepted"

    def test_an_empty_optional_field_does_not_fail_the_citation(self):
        records = [
            {"title": "S1", "geo_accession": "GSM1", "condition": "treatment: a"},
            {"title": "S2", "geo_accession": "GSM2", "condition": ""},
        ]
        mapping = [
            {
                "column": "S1",
                "arm": "test",
                "biological_unit": "GSM1",
                "evidence": [{"source": "sample_record", "quote": record_reference(records[0])}],
            },
            {
                "column": "S2",
                "arm": "reference",
                "biological_unit": "GSM2",
                "evidence": [{"source": "sample_record", "quote": record_reference(records[1])}],
            },
        ]
        assert validate_mapping(mapping, columns=["S1", "S2"], sample_records=records)["status"] == "accepted"


class TestOtherSourcesKeepTheirIdentity:
    def test_a_methods_quotation_is_checked_against_the_text_it_was_given(self):
        records = [
            {"title": "S1", "geo_accession": "GSM1", "condition": "treatment: a"},
            {"title": "S2", "geo_accession": "GSM2", "condition": "treatment: b"},
        ]
        mapping = [
            {
                "column": "S1",
                "arm": "test",
                "biological_unit": "GSM1",
                "evidence": [{"source": "methods", "quote": "cultures were treated for 24 hours"}],
            },
            {
                "column": "S2",
                "arm": "reference",
                "biological_unit": "GSM2",
                "evidence": [{"source": "methods", "quote": "cultures were left untreated"}],
            },
        ]
        good = validate_mapping(
            mapping,
            columns=["S1", "S2"],
            sample_records=records,
            texts=["cultures were treated for 24 hours; cultures were left untreated"],
        )
        assert good["status"] == "accepted"
        bad = validate_mapping(mapping, columns=["S1", "S2"], sample_records=records, texts=["nothing like it"])
        assert bad["status"] == "unresolved"
        assert any("not in the text" in r for r in bad["reasons"])

    def test_a_column_name_quotation_must_be_the_columns_own_name(self):
        records = [{"title": "S1", "geo_accession": "GSM1", "condition": "a"}]
        mapping = [
            {
                "column": "S1",
                "arm": "test",
                "biological_unit": "GSM1",
                "evidence": [{"source": "column_name", "quote": "S2"}],
            },
            {
                "column": "S2",
                "arm": "reference",
                "biological_unit": "unit",
                "evidence": [{"source": "column_name", "quote": "S2"}],
            },
        ]
        result = validate_mapping(mapping, columns=["S1", "S2"], sample_records=records)
        assert any("is not the name of column S1" in r for r in result["reasons"])
