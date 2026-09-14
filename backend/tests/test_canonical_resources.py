"""plan_8_2 section 2.2: one canonical, typed resource inventory, whoever reads it.

Study 44's report listed "GSM2454338 and GSM2454339" as an unrecognised archive, "table S1" beside
"Supplemental Table S1", and PDB structures that the resource table called structures and the resource
statements called an unknown archive. One sentence read "bioAF has an archive bioAF does not recognise",
another "bioAF has no other adapter". Compound identifiers are split, GEO sample records kept as samples,
supplement aliases reconciled through the manifest's identity, and support stated as separate facts, in
one pass shared by the read, the report and the resource statements. The rows below have the shape of
study 44's; nothing here is a rule about that paper.
"""

import pytest

from app.services.validation_resource_identity import canonical_resources


def _row(identifier, archive, type_, role=None, **extra):
    return {
        "id": None,
        "identifier": identifier,
        "archive": archive,
        "type": type_,
        "role": role,
        "stated_in": None,
        "found_by": ["model"],
        "reported_experiment_ids": [],
        "linked_by": None,
        "bioaf": {"retrievable": "unknown", "analyzable": "unknown", "limitation": None},
        **extra,
    }


_LEGACY = [
    _row("6LUI", "pdb", "structure", "crystal structure"),
    _row("GSE144396", "geo", "sequencing_data", "ChIP-seq and RNA-seq data", reported_experiment_ids=["e1", "e2"]),
    _row("PXD016781", "pride", "proteomics_data", "Mass spectrometry data"),
    _row("STI20A", "other", "binding_assay", "PBM data"),
    _row("GSM2454338 and GSM2454339", "other", "sequencing_data", "CXXC1 ChIP-seq"),
    _row("table S1", "other", "supplementary_file", "RT-qPCR and gene-specific primers"),
    _row("Supplemental Table S1", "journal_supplement", "supplementary_file", None, found_by=["supplement_manifest"]),
]


def _by_identifier(rows):
    return {r["identifier"]: r for r in rows}


class TestIdentity:
    def test_a_compound_identifier_becomes_one_row_per_identifier(self):
        rows = _by_identifier(canonical_resources(_LEGACY))
        assert "GSM2454338 and GSM2454339" not in rows
        for gsm in ("GSM2454338", "GSM2454339"):
            row = rows[gsm]
            assert row["archive"] == "geo" and row["level"] == "sample"
            assert row["role"] == "CXXC1 ChIP-seq"
            assert row["split_from"] == "GSM2454338 and GSM2454339"

    def test_a_geo_sample_record_is_never_an_unrecognised_archive(self):
        row = _by_identifier(canonical_resources(_LEGACY))["GSM2454338"]
        assert row["support"]["recognized"] == "yes"
        assert "does not recognise" not in (row["limitation"] or "")
        assert "GEO sample record" in row["limitation"]

    def test_a_supplement_alias_joins_the_supplement_it_names(self):
        rows = canonical_resources(_LEGACY)
        supplements = [r for r in rows if r["type"] == "supplementary_file"]
        assert [r["identifier"] for r in supplements] == ["Supplemental Table S1"]
        (supplement,) = supplements
        assert supplement["archive"] == "journal_supplement"
        assert "table S1" in supplement["references"]
        assert supplement["role"] == "RT-qPCR and gene-specific primers"
        assert set(supplement["found_by"]) == {"model", "supplement_manifest"}

    def test_an_alias_with_no_manifest_row_is_still_a_journal_supplement(self):
        rows = _by_identifier(canonical_resources([_row("table S7", "other", "supplementary_file", "primers")]))
        assert rows["table S7"]["archive"] == "journal_supplement"
        assert "does not recognise" not in (rows["table S7"]["limitation"] or "")

    def test_a_pdb_structure_stays_a_pdb_structure(self):
        row = _by_identifier(canonical_resources(_LEGACY))["6LUI"]
        assert (row["archive"], row["type"]) == ("pdb", "structure")
        assert row["link"] == "https://www.rcsb.org/structure/6LUI"
        assert row["limitation"].startswith("bioAF has no PDB adapter")

    def test_the_canonical_inventory_is_stable_when_read_again(self):
        once = canonical_resources(_LEGACY)
        assert canonical_resources(once) == once


class TestWords:
    def test_no_sentence_names_bioaf_as_having_an_archive_or_an_other_adapter(self):
        for row in canonical_resources(_LEGACY):
            words = row["limitation"] or ""
            assert "bioAF has an archive" not in words and "no other adapter" not in words, words

    def test_an_unrecognised_archive_is_named_as_bioafs_limitation(self):
        row = _by_identifier(canonical_resources(_LEGACY))["STI20A"]
        assert row["limitation"].startswith("bioAF does not recognise the archive that holds STI20A")


class TestSupportFacts:
    def test_support_is_stated_as_separate_facts(self):
        rows = _by_identifier(
            canonical_resources(
                _LEGACY,
                deposits=[{"accession": "GSE144396", "archive": "geo", "exists": "yes", "access": "public"}],
            )
        )
        geo = rows["GSE144396"]["support"]
        assert geo == {
            "recognized": "yes",
            "metadata_verified": "yes",
            "download_supported": "unknown",
            "analysis_supported": "unknown",
            "access": "public",
        }
        pride = rows["PXD016781"]["support"]
        assert (pride["recognized"], pride["metadata_verified"], pride["download_supported"]) == (
            "yes",
            "unknown",
            "no",
        )

    def test_an_unsupported_archive_keeps_its_link_and_the_experiments_it_serves(self):
        rows = _by_identifier(
            canonical_resources([_row("PXD016781", "pride", "proteomics_data", reported_experiment_ids=["e4"])])
        )
        assert rows["PXD016781"]["link"] == "https://www.ebi.ac.uk/pride/archive/projects/PXD016781"
        assert rows["PXD016781"]["reported_experiment_ids"] == ["e4"]


class TestTheOtherSurfacesReadTheSameIdentity:
    def test_the_report_lists_the_canonical_rows(self):
        from app.services.validation_report_summary import summarize

        summary = summarize(
            study={"state": "classified"}, evidence={}, plan={"resources": _LEGACY}, targets=[], issues=[]
        )
        identifiers = [r["identifier"] for r in summary["resources"]]
        assert "GSM2454338" in identifiers and "GSM2454338 and GSM2454339" not in identifiers
        assert "table S1" not in identifiers
        pdb = next(r for r in summary["resources"] if r["identifier"] == "6LUI")
        assert pdb["archive"] == "pdb" and pdb["link"] and pdb["support"]["recognized"] == "yes"

    def test_a_resource_statement_names_the_archive_the_inventory_names(self):
        from app.services.validation_rubric_v2 import resource_statements

        plan = {"resources": [dict(_LEGACY[0])], "reported_experiments": [{"id": "e1", "resources": ["6LUI"]}]}
        evidence = {
            "capabilities": {
                "deposits": [
                    {
                        "accession": "6LUI",
                        "archive": "other",
                        "exists": "unknown",
                        "failure_reason": (
                            "bioAF cannot look up deposits in an archive bioAF does not recognise, so what 6LUI holds is "
                            "unknown"
                        ),
                    }
                ]
            }
        }
        (statement,) = resource_statements(plan, evidence)
        assert statement["archive"] == "pdb"
        details = " ".join(c.get("detail") or "" for c in statement["checks"])
        assert "does not recognise" not in details and "PDB" in details


class TestDiscoveryKeepsTheArchiveContextEstablished:
    @pytest.mark.asyncio
    async def test_a_code_the_text_scan_placed_in_pdb_is_described_as_pdb_whoever_named_it_first(self):
        from app.services.validation_capabilities import discover_capabilities

        async def offline(url):
            raise AssertionError(f"no lookup is made for a PDB code: {url}")

        capabilities = await discover_capabilities(
            accessions=[
                {"accession": "6LUI", "provenance": "extracted"},
                {"accession": "6LUI", "provenance": "text_scan", "archive": "pdb"},
            ],
            has_full_text=True,
            code_availability=[],
            fetcher=offline,
        )
        (deposit,) = capabilities["deposits"]
        assert deposit["archive"] == "pdb"
        assert "does not recognise" not in (deposit["failure_reason"] or "")


class TestTheReadBuildsTheCanonicalInventory:
    def test_the_read_splits_named_samples_and_reconciles_supplement_aliases(self):
        from app.services.resource_inventory import build_resource_inventory

        rows = build_resource_inventory(
            scanned=[{"identifier": "6LUI", "archive": "pdb"}],
            model_resources=[
                {"identifier": "GSM2454338 and GSM2454339", "type": "sequencing_data", "role": "CXXC1 ChIP-seq"},
                {"identifier": "GSM1000001", "type": "sequencing_data", "role": "MTF2 ChIP-seq"},
                {"identifier": "table S1", "type": "supplementary_file", "role": "primers"},
            ],
            extracted_accessions=["GSE144396", "GSM2000002"],
            supplements=[{"label": "Supplemental Table S1", "filename": "s1.xlsx", "kind": "attachment"}],
            deposits=[],
            experiments=[],
        )
        identifiers = [r["identifier"] for r in rows]
        assert identifiers.count("Supplemental Table S1") == 1 and "table S1" not in identifiers
        assert {"GSM2454338", "GSM2454339", "GSM1000001"} <= set(identifiers)
        # A bare sample accession belongs to its deposit and is not listed on its own.
        assert "GSM2000002" not in identifiers
        pdb = next(r for r in rows if r["identifier"] == "6LUI")
        assert pdb["archive"] == "pdb" and pdb["support"]["recognized"] == "yes"
        assert all("does not recognise" not in (r["limitation"] or "") for r in rows if r["archive"] != "other")
