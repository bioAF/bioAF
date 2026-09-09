"""change_7.1 section 3: bind claims against the evidence, not against a bare metric name.

`build_binding_prompt` sent one line per claim: key, value, unit, locator. Nothing else. The model
was asked which controlled metric "transcripts detected = 10500" measures without being shown the
sentence it came from, the sample table that says 54 samples were collected and 51 analysed, or the
results table whose 194 rows include the 88 that clear the fold-change cutoff.

The digest is bounded on purpose. Column headers, row counts and threshold splits are a few hundred
bytes whatever the supplement's size, so a paper with a 20,000-row matrix costs the same as one with
none, and a number buried in row 5000 binds unresolved rather than wrong.
"""

from app.services.supplement_inventory import build_inventory_digest
from app.services.validation_extraction_service import build_binding_prompt

_S1_ROWS = [
    {"ProcessingID": "NRAG44", "Sampletype": "wholeembryo", "MorphokineticCall": "BAD", "Sex": "XY"},
    {"ProcessingID": "NRAG42", "Sampletype": "trophectodermbiopsy", "MorphokineticCall": "GOOD", "Sex": "XX"},
]


class TestTheClaimCarriesItsOwnSentence:
    def test_the_passage_reaches_the_model(self):
        _system, payload = build_binding_prompt(
            [
                {
                    "metric_key": "transcripts detected",
                    "value": 10500,
                    "claim_text": "a mean of 10,500 transcripts per whole embryo after QC exclusions",
                }
            ]
        )
        assert "10,500 transcripts per whole embryo" in payload

    def test_a_claim_without_a_passage_still_binds(self):
        _system, payload = build_binding_prompt([{"metric_key": "alignment_rate", "value": 83.4}])
        assert "alignment_rate" in payload


class TestTheInventoryDigestIsBounded:
    def test_it_names_each_supplement_and_its_role(self):
        digest = build_inventory_digest(
            [
                {"label": "Supplemental File S1", "role": "sample_metadata", "resolved": True},
                {"label": "Supplemental File S3", "role": "results_table", "resolved": True},
            ]
        )
        assert "Supplemental File S1" in digest
        assert "sample_metadata" in digest

    def test_it_reports_row_counts_and_columns_not_rows(self):
        """The counts are the evidence. The rows themselves are not, and a 20,000-row matrix must
        not change what this costs."""
        digest = build_inventory_digest(
            [
                {
                    "label": "Supplemental File S3",
                    "role": "results_table",
                    "resolved": True,
                    "row_count": 194,
                    "columns": ["info", "baseMean", "log2FoldChange", "pvalue", "padj", "chr"],
                    "threshold_splits": {"abs_log2fc>2": 88, "chrX_or_chrY": 146},
                }
            ]
        )
        assert "194" in digest
        assert "88" in digest
        assert "log2FoldChange" in digest
        assert "ENSG" not in digest

    def test_it_stays_small_whatever_the_supplement_size(self):
        digest = build_inventory_digest(
            [
                {
                    "label": f"Supplemental File S{i}",
                    "role": "expression_matrix",
                    "resolved": True,
                    "row_count": 20000,
                    "columns": [f"sample_{j}" for j in range(200)],
                }
                for i in range(5)
            ]
        )
        assert len(digest) < 4000

    def test_an_unresolved_reference_is_said_to_be_unresolved(self):
        digest = build_inventory_digest([{"label": "Supplemental File S2", "role": "unknown", "resolved": False}])
        assert "not been retrieved" in digest

    def test_no_supplements_yields_no_digest(self):
        assert build_inventory_digest([]) == ""


class TestTheDigestReachesTheBindingCall:
    def test_the_payload_carries_the_inventory(self):
        digest = build_inventory_digest(
            [{"label": "Supplemental File S1", "role": "sample_metadata", "resolved": True, "row_count": 54}]
        )
        _system, payload = build_binding_prompt([{"metric_key": "samples", "value": 54}], inventory=digest)
        assert "Supplemental File S1" in payload
        assert "54" in payload

    def test_the_model_is_told_the_inventory_can_correct_a_claim(self):
        system, _payload = build_binding_prompt([{"metric_key": "samples", "value": 54}], inventory="anything")
        assert "supplement" in system.lower()
