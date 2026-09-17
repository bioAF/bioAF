"""plan_8_3 stage 4: one versioned reading of a deposited matrix's columns, shared by every consumer.

Study 50's deposit begins `GeneType`, `GeneSymbol`, `ENSG`, then 21 measurement columns. bioAF read
the first column as the feature identifier and everything after it as a sample, so it reported 23
samples, gave two annotation columns zero library sizes, refused the mapping because two "samples"
were unassigned, and would have sent a biotype into the analysis template as the gene identifier.

Roles are established once, from the header, the content and the repository's sample records, and
every later reader uses that reading. No reader may fall back to the first column and the rest.
"""

import json
import pathlib

import pytest

from app.services.matrix_interpretation import (
    ANNOTATION,
    FEATURE_ID,
    INTERPRETATION_VERSION,
    MEASUREMENT,
    interpret_matrix,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "panahipour"


def _matrix() -> str:
    return (_FIXTURE / "raw_counts_excerpt.tsv").read_text(encoding="utf-8")


def _records() -> list[dict]:
    return json.loads((_FIXTURE / "sample_records.json").read_text(encoding="utf-8"))


def _roles(interpretation) -> dict[str, str]:
    return {c["name"]: c["role"] for c in interpretation["columns"]}


class TestTheRegressionDeposit:
    def test_the_ensembl_column_is_the_feature_identifier_and_the_other_two_are_annotations(self):
        roles = _roles(interpret_matrix(_matrix(), sample_records=_records()))
        assert roles["ENSG"] == FEATURE_ID
        assert roles["GeneType"] == ANNOTATION
        assert roles["GeneSymbol"] == ANNOTATION

    def test_the_twenty_one_repository_linked_columns_are_the_measurements(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records())
        assert len(interpretation["sample_columns"]) == 21
        assert all(c.startswith("LP") for c in interpretation["sample_columns"])
        assert interpretation["status"] == "established"

    def test_an_annotation_column_never_enters_a_library_size(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records())
        assert set(interpretation["library_sizes"]) == set(interpretation["sample_columns"])
        assert interpretation["zero_columns"] == []

    def test_the_roles_hold_without_the_repository_records_too(self):
        """A preview is read before the records are scoped, so the header and content must settle it."""
        roles = _roles(interpret_matrix(_matrix()))
        assert roles["ENSG"] == FEATURE_ID
        assert roles["GeneType"] == ANNOTATION
        assert roles["GeneSymbol"] == ANNOTATION
        assert len([c for c, r in roles.items() if r == MEASUREMENT]) == 21

    def test_it_says_what_established_each_role(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records())
        by_name = {c["name"]: c for c in interpretation["columns"]}
        assert by_name["LP06001"]["basis"] == "repository_record"
        assert by_name["GeneType"]["basis"]
        assert by_name["ENSG"]["basis"]

    def test_it_records_its_version_the_source_checksum_and_the_identifier_namespace(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records(), source_checksum="abc123")
        assert interpretation["version"] == INTERPRETATION_VERSION
        assert interpretation["source_checksum"] == "abc123"
        assert interpretation["feature_namespace"] == "ensembl_gene"
        assert interpretation["format"] == "tsv"
        assert interpretation["orientation"] == "features_in_rows"

    def test_a_repeated_symbol_and_a_blank_symbol_are_reported_not_collapsed(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records())
        assert interpretation["duplicate_feature_ids"] == []
        assert interpretation["rows_without_feature_id"] == 0
        assert interpretation["transformations"] == []


class TestAPlainIdentifierAndCountsMatrix:
    def test_its_one_text_column_is_the_identifier_and_the_rest_are_samples(self):
        text = "gene_id\tS1\tS2\nENSG00000001\t5\t7\nENSG00000002\t0\t3\n"
        interpretation = interpret_matrix(text)
        assert interpretation["feature_column"] == "gene_id"
        assert interpretation["sample_columns"] == ["S1", "S2"]

    def test_an_unnamed_index_column_is_still_the_identifier(self):
        text = "\tS1\tS2\nENSG00000001\t5\t7\nENSG00000002\t0\t3\n"
        interpretation = interpret_matrix(text)
        assert interpretation["feature_column"] == ""
        assert interpretation["sample_columns"] == ["S1", "S2"]

    def test_a_comma_separated_matrix_reads_the_same_way(self):
        interpretation = interpret_matrix("gene_id,S1,S2\nENSG00000001,5,7\nENSG00000002,0,3\n")
        assert interpretation["format"] == "csv"
        assert interpretation["sample_columns"] == ["S1", "S2"]


class TestNumericColumnsThatAreNotSamples:
    def test_a_length_and_a_coordinate_column_are_annotations(self):
        text = (
            "gene_id\tchromosome\tstart\tend\tlength\tS1\tS2\n"
            "ENSG00000001\tchr1\t11869\t14409\t2540\t5\t7\n"
            "ENSG00000002\tchr1\t14404\t29570\t15166\t0\t3\n"
        )
        interpretation = interpret_matrix(text)
        assert interpretation["sample_columns"] == ["S1", "S2"]
        assert [c["name"] for c in interpretation["columns"] if c["role"] == ANNOTATION] == [
            "chromosome",
            "start",
            "end",
            "length",
        ]

    def test_annotations_after_the_samples_are_still_annotations(self):
        text = "gene_id\tS1\tS2\tgene_biotype\nENSG00000001\t5\t7\tprotein_coding\nENSG00000002\t0\t3\tlncRNA\n"
        interpretation = interpret_matrix(text)
        assert interpretation["sample_columns"] == ["S1", "S2"]
        assert _roles(interpretation)["gene_biotype"] == ANNOTATION


class TestWhatStaysUnresolved:
    def test_a_matrix_with_no_column_of_distinct_identifiers_is_unresolved(self):
        text = "biotype\tS1\tS2\nprotein_coding\t5\t7\nprotein_coding\t0\t3\nprotein_coding\t2\t4\n"
        interpretation = interpret_matrix(text)
        assert interpretation["status"] == "unresolved"
        assert interpretation["unresolved_kind"] == "feature_id"
        assert interpretation["reason"]

    def test_a_matrix_with_no_measurement_column_is_unresolved(self):
        interpretation = interpret_matrix("gene_id\tgene_biotype\nENSG00000001\tprotein_coding\n")
        assert interpretation["status"] == "unresolved"
        assert interpretation["unresolved_kind"] == "measurements"

    def test_a_repeated_column_name_is_unresolved_rather_than_silently_one_column(self):
        text = "gene_id\tS1\tS1\nENSG00000001\t5\t7\n"
        interpretation = interpret_matrix(text)
        assert interpretation["status"] == "unresolved"
        assert interpretation["unresolved_kind"] == "duplicate_columns"

    def test_a_transposed_matrix_is_unsupported_and_says_so(self):
        text = "sample\tENSG00000001\tENSG00000002\nS1\t5\t7\nS2\t0\t3\n"
        interpretation = interpret_matrix(text, sample_records=[{"title": "S1"}, {"title": "S2"}])
        assert interpretation["orientation"] == "samples_in_rows"
        assert interpretation["status"] == "unresolved"
        assert interpretation["unresolved_kind"] == "orientation"

    def test_a_table_with_no_data_rows_is_unresolved(self):
        assert interpret_matrix("gene_id\tS1\n")["unresolved_kind"] == "no_rows"


class TestWhatTheNumbersMean:
    def test_a_genuine_all_zero_sample_is_a_sample_with_a_zero_library_not_an_annotation(self):
        text = "gene_id\tS1\tS2\tS3\nENSG00000001\t5\t0\t7\nENSG00000002\t3\t0\t1\n"
        interpretation = interpret_matrix(text)
        assert interpretation["sample_columns"] == ["S1", "S2", "S3"]
        assert interpretation["library_sizes"]["S2"] == 0
        assert interpretation["zero_columns"] == ["S2"]
        assert interpretation["columns_without_observations"] == []

    def test_a_column_with_no_numeric_observation_at_all_is_named_separately(self):
        text = "gene_id\tS1\tnote\nENSG00000001\t5\tkept\nENSG00000002\t3\tkept\n"
        interpretation = interpret_matrix(text, sample_records=[{"title": "S1"}, {"title": "note"}])
        assert interpretation["columns_without_observations"] == ["note"]
        assert "note" not in interpretation["sample_columns"]

    def test_missing_and_nonfinite_cells_are_counted_not_coerced_to_zero(self):
        text = "gene_id\tS1\tS2\nENSG00000001\t5\t\nENSG00000002\tNA\tNaN\nENSG00000003\t2\tInf\n"
        interpretation = interpret_matrix(text)
        assert interpretation["library_sizes"]["S1"] == 7
        assert interpretation["missing_cells"] == 2
        assert interpretation["nonfinite_cells"] == 2
        assert interpretation["columns_without_observations"] == ["S2"]

    def test_per_million_columns_are_not_read_as_counts(self):
        rows = "\n".join(f"ENSG0000{i:04d}\t{1_000_000 / 100}\t{1_000_000 / 100}" for i in range(100))
        interpretation = interpret_matrix(f"gene_id\tS1\tS2\n{rows}\n")
        assert interpretation["value_type"] == "tpm_or_cpm"

    def test_nonnegative_integers_alone_are_not_proof_of_raw_counts(self):
        """The observation and the deposit's own statement are kept apart; neither overwrites the other."""
        interpretation = interpret_matrix(
            "gene_id\tS1\tS2\nENSG00000001\t5\t7\n", claimed_value_type="normalized_other"
        )
        assert interpretation["value_type"] == "counts"
        assert interpretation["value_type_claimed"] == "normalized_other"
        assert interpretation["value_type_disagrees"] is True


class TestDuplicateAndMissingFeatureIdentifiers:
    def test_duplicate_identifiers_are_reported_and_the_original_text_is_preserved(self):
        text = "gene_id\tS1\nENSG00000001.5\t5\nENSG00000001.5\t7\nENSG00000002.1\t3\n"
        interpretation = interpret_matrix(text)
        assert interpretation["duplicate_feature_ids"] == ["ENSG00000001.5"]
        assert interpretation["transformations"] == []
        assert interpretation["status"] == "established"

    def test_rows_with_no_identifier_are_counted(self):
        text = "gene_id\tS1\nENSG00000001\t5\n\t7\nENSG00000002\t3\n"
        interpretation = interpret_matrix(text)
        assert interpretation["rows_without_feature_id"] == 1


class TestAProposedReadingIsChecked:
    def test_a_proposal_that_names_the_biotype_as_the_identifier_is_refused(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records(), proposal={"feature_column": "GeneType"})
        assert interpretation["feature_column"] == "ENSG"
        assert any("GeneType" in note for note in interpretation["notes"])

    def test_a_proposal_that_agrees_with_the_content_is_recorded_as_agreeing(self):
        interpretation = interpret_matrix(_matrix(), sample_records=_records(), proposal={"feature_column": "ENSG"})
        assert interpretation["feature_column"] == "ENSG"
        assert interpretation["notes"] == []


class TestOneReadingForEveryConsumer:
    def test_the_inspection_reports_the_interpretations_columns_and_identifier(self):
        from app.services.deposit_inspection import inspect_matrix

        inspection = inspect_matrix(_matrix(), sample_records=_records())
        assert inspection["id_column"] == "ENSG"
        assert inspection["n_columns"] == 21
        assert "GeneSymbol" not in inspection["columns"]
        assert inspection["zero_columns"] == []
        assert inspection["interpretation"]["version"] == INTERPRETATION_VERSION

    def test_the_inspection_holds_an_unusable_matrix_whose_roles_are_unresolved(self):
        from app.services.deposit_inspection import inspect_matrix

        inspection = inspect_matrix(
            "biotype\tS1\tS2\nprotein_coding\t5\t7\nprotein_coding\t0\t3\nprotein_coding\t2\t4\n"
        )
        assert inspection["usable"] is False
        assert inspection["unusable_reason"]

    @pytest.mark.parametrize("bad", ["", "gene_id\n", "\n\n"])
    def test_the_inspection_never_raises_on_something_that_is_not_a_matrix(self, bad):
        from app.services.deposit_inspection import inspect_matrix

        assert inspect_matrix(bad)["usable"] is False
