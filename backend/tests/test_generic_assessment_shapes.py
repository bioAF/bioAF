"""change_7.1 section 8: the behaviour follows the evidence, not one paper's strings.

Every value here is deliberately unlike Groff's: different accessions, archives, filenames,
identifiers, thresholds and counts. If any of these pass only because a constant somewhere spells
`EGAS00001003667`, `194` or `Supplemental File S3`, that constant is a paper-specific rule in
production code and this file is where it shows.
"""

import io
import zipfile

import pytest

from app.services.archive_discovery import describe_deposit
from app.services.supplement_inventory import (
    build_inventory_digest,
    measure_table,
    parse_jats_supplements,
    resolve_supplements,
)
from app.services.validation_completion import completion_for
from app.services.validation_precompute_checks import check_sample_data

# A different journal's markup, a different naming convention, different identifiers.
_JATS = (
    '<?xml version="1.0"?><article><body><sec>'
    "<p>Cohort characteristics are in Additional File 2 and the differential output in "
    "Supplementary Table S7.</p>"
    "</sec></body></article>"
)

_COHORT = (
    "donor_id\ttissue\tcondition\tsex\nD01\tliver\ttreated\tF\nD02\tliver\tcontrol\tM\nD03\tkidney\ttreated\tF\n"
).encode()

_DIFFERENTIAL = (
    "gene_id\tlogFC\tFDR\tband\nENSG1\t3.10\t0.001\t8q24\nENSG2\t-1.20\t0.010\t8q24\nENSG3\t0.40\t0.200\t17p13\n"
).encode()


def _bundle() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("12345_2021_Additional_File_2_cohort.txt", _COHORT)
        zf.writestr("12345_2021_Supplementary_Table_7_de.txt", _DIFFERENTIAL)
    return buffer.getvalue()


async def _fetch(_url):
    return _bundle()


class TestAPaperWithNoDepositButPublicAttachments:
    def test_its_supplements_are_named_from_its_own_wording(self):
        # change_7.3 section 2 (flagged test change): this accepted "Supplemental File S7" for the
        # paper's "Supplementary Table S7", which is the every-kind-becomes-File collapse the section
        # removes, and "Additional File 2" was never recognised at all. The noun is kept now.
        labels = {s["label"] for s in parse_jats_supplements(_JATS)}
        assert labels == {"Additional File 2", "Supplemental Table S7"}

    @pytest.mark.asyncio
    async def test_a_different_naming_convention_still_resolves(self):
        resolved = await resolve_supplements("PMC999999", parse_jats_supplements(_JATS), fetcher=_fetch)
        assert any(s.get("filename", "").endswith("cohort.txt") for s in resolved if s.get("filename"))

    @pytest.mark.asyncio
    async def test_roles_come_from_content_not_from_the_filename(self):
        resolved = await resolve_supplements("PMC999999", parse_jats_supplements(_JATS), fetcher=_fetch)
        by_file = {s["filename"]: s["role"] for s in resolved if s.get("filename")}
        assert by_file["12345_2021_Additional_File_2_cohort.txt"] == "sample_metadata"
        assert by_file["12345_2021_Supplementary_Table_7_de.txt"] == "results_table"


class TestADifferentThresholdIsMeasured:
    def test_a_one_and_a_half_fold_cutoff(self):
        measured = measure_table(_DIFFERENTIAL, thresholds=[1.5])
        assert measured["threshold_splits"] == {"abs_log2fc>1.5": 1}

    def test_a_column_named_differently_is_still_the_fold_change(self):
        """`logFC` is edgeR's name for what DESeq2 calls `log2FoldChange`."""
        assert measure_table(_DIFFERENTIAL, thresholds=[1.0])["threshold_splits"]["abs_log2fc>1"] == 2

    def test_a_category_column_that_is_not_a_chromosome(self):
        """Cytogenetic bands, tissues, conditions: the counting is not about chromosomes."""
        assert measure_table(_DIFFERENTIAL, thresholds=[])["category_counts"]["band"] == {
            "17p13": 1,
            "8q24": 2,
        }


class TestASupportedPublicDeposit:
    @pytest.mark.asyncio
    async def test_a_public_geo_series_is_acquirable(self):
        async def _geo(url):
            if "filereport" in url:
                return (
                    "run_accession\texperiment_accession\tsample_accession\tsample_title\t"
                    "library_strategy\tfastq_bytes\nSRR9\tSRX9\tSAMN9\tLiver 1\tRNA-Seq\t111;222\n"
                )
            if "series_matrix" in url or url.endswith("/matrix/"):
                return '!Sample_title\t"Liver 1"\n!Sample_geo_accession\t"GSM99"\n'
            raise RuntimeError(f"404 {url}")

        deposit = await describe_deposit("GSE900001", provenance="requested", scoped=True, fetcher=_geo)
        assert deposit["archive"] == "geo"
        assert deposit["supported"] == "yes"


class TestAnUnsupportedSource:
    @pytest.mark.asyncio
    async def test_it_is_unknown_rather_than_absent(self):
        async def _nothing(_url):
            raise RuntimeError("connection reset")

        deposit = await describe_deposit("E-MTAB-9999", provenance="extracted", scoped=True, fetcher=_nothing)
        assert deposit["exists"] == "unknown"
        assert deposit["failure_reason"]


class TestTheOutcomeIsChosenPerPaper:
    def test_an_unsupported_archive_holding_the_data_is_not_missing_data(self):
        outcome = completion_for(
            route="pipeline",
            capabilities={
                "raw_data": {"value": "yes"},
                "deposits": [
                    {
                        "archive": "arrayexpress",
                        "accession": "E-MTAB-9999",
                        "exists": "yes",
                        "access": "public",
                        "supported": "no",
                        "raw_data": "yes",
                    }
                ],
            },
            supplements=[],
        )
        assert outcome["classification"] == "access_restricted"
        assert "E-MTAB-9999" in outcome["reason"]

    def test_a_paper_that_deposited_nothing_is_missing_data(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"raw_data": {"value": "no"}, "preprocessed_data": {"value": "no"}, "deposits": []},
            supplements=[],
        )
        assert outcome["classification"] == "missing_data"

    def test_a_results_table_under_a_different_name_still_counts(self):
        outcome = completion_for(
            route="deposit",
            capabilities={"preprocessed_data": {"value": "no"}, "deposits": []},
            supplements=[{"label": "Supplementary Table S7", "role": "results_table", "resolved": True}],
        )
        assert outcome["processed_results_available"] is True
        assert "Supplementary Table S7" in outcome["reason"]


class TestTheChecksUseWhateverMetadataExists:
    def test_a_cohort_table_answers_the_sample_count(self):
        result = check_sample_data(
            paper_sample_count=3,
            entries=[],
            supplements=[{"label": "Additional File 2", "role": "sample_metadata", "resolved": True, "row_count": 3}],
        )
        assert result["verdict"] == "ok"
        assert "Additional File 2" in result["detail"]


class TestTheDigestDescribesWhateverWasFound:
    def test_it_names_this_paper_s_supplements(self):
        digest = build_inventory_digest(
            [
                {
                    "label": "Supplementary Table S7",
                    "role": "results_table",
                    "resolved": True,
                    "row_count": 3,
                    "threshold_splits": {"abs_log2fc>1.5": 1},
                },
            ]
        )
        assert "Supplementary Table S7" in digest
        assert "abs_log2fc>1.5" in digest
        assert "194" not in digest
