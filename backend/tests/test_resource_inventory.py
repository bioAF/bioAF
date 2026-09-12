"""change_7.5 section 2.1: one typed record per resource the paper names, kept whether or not bioAF can use it.

Study 38 showed PXD016781 as "an archive bioAF does not recognise" and never listed its RNA-seq
deposit, so the authors' result tables stayed invisible. Discovery is separate from bioAF's ability to
use a resource: an unsupported archive stays visible with its role and its limitation.
"""

from app.services.resource_inventory import (
    STRUCTURE,
    PROTEOMICS_DATA,
    SEQUENCING_DATA,
    CODE,
    OTHER_DATA,
    SUPPLEMENTARY_FILE,
    build_resource_inventory,
)
from app.services.validation_report_summary import summarize


def _inventory(**kw):
    return build_resource_inventory(
        scanned=kw.get("scanned", []),
        model_resources=kw.get("model_resources", []),
        extracted_accessions=kw.get("extracted_accessions", []),
        supplements=kw.get("supplements", []),
        deposits=kw.get("deposits", []),
        experiments=kw.get("experiments", []),
    )


def test_the_identifier_decides_the_type_wherever_it_can():
    resources = _inventory(
        scanned=[
            {"identifier": "GSE555001", "archive": "geo"},
            {"identifier": "PXD099001", "archive": "pride"},
            {"identifier": "7ABC", "archive": "pdb"},
            {"identifier": "https://github.com/lab/code", "archive": "github"},
        ],
        # The model called the PRIDE deposit sequencing data; the identifier says otherwise.
        model_resources=[{"identifier": "PXD099001", "type": "sequencing_data", "role": "the proteome"}],
    )
    by_id = {r["identifier"]: r for r in resources}
    assert by_id["GSE555001"]["type"] == SEQUENCING_DATA
    assert by_id["PXD099001"]["type"] == PROTEOMICS_DATA
    assert by_id["7ABC"]["type"] == STRUCTURE
    assert by_id["https://github.com/lab/code"]["type"] == CODE
    assert by_id["PXD099001"]["role"] == "the proteome"


def test_the_model_decides_the_type_only_where_the_identifier_does_not():
    resources = _inventory(
        scanned=[{"identifier": "10.5281/zenodo.555004", "archive": "zenodo"}],
        model_resources=[{"identifier": "10.5281/zenodo.555004", "type": "code", "role": "analysis scripts"}],
    )
    assert resources[0]["type"] == CODE
    silent = _inventory(scanned=[{"identifier": "10.5281/zenodo.555004", "archive": "zenodo"}])
    assert silent[0]["type"] == OTHER_DATA


def test_an_unsupported_archive_is_listed_with_its_role_and_limitation():
    [pride] = _inventory(
        model_resources=[
            {
                "identifier": "PXD099001",
                "type": "proteomics_data",
                "role": "mass spectrometry of the knockout",
                "stated_in": "data availability",
            }
        ]
    )
    assert pride["archive"] == "pride"
    assert pride["role"] == "mass spectrometry of the knockout"
    assert pride["stated_in"] == "data availability"
    assert pride["bioaf"]["retrievable"] == "no"
    assert "no PRIDE adapter" in pride["bioaf"]["limitation"]


def test_where_each_resource_was_found_is_kept():
    resources = _inventory(
        scanned=[{"identifier": "GSE555001", "archive": "geo"}],
        extracted_accessions=["GSE555001"],
        supplements=[{"label": "Supplemental Table S1", "filename": "t1.xlsx", "kind": "attachment", "role": "results_table", "resolved": True}],
    )
    by_id = {r["identifier"]: r for r in resources}
    assert set(by_id["GSE555001"]["found_by"]) == {"text_scan", "model"}
    supplement = by_id["Supplemental Table S1"]
    assert supplement["type"] == SUPPLEMENTARY_FILE
    assert supplement["found_by"] == ["supplement_manifest"]
    assert supplement["bioaf"]["analyzable"] == "yes"


def test_a_listed_deposit_carries_a_digest_of_what_it_holds():
    [geo] = _inventory(
        scanned=[{"identifier": "GSE555001", "archive": "geo"}],
        deposits=[
            {
                "accession": "GSE555001",
                "archive": "geo",
                "exists": "yes",
                "supported": "yes",
                "access": "public",
                "raw_data": "yes",
                "preprocessed_data": "yes",
                "registered_samples": 12,
                "result_tables": ["GSE555001_DESeq2_KOvsWT.txt.gz"],
                "listing": {"kinds": {"matrix_normalized": 1, "de_table": 1}, "library_strategies": ["RNA-Seq"]},
            }
        ],
    )
    assert geo["listing"]["result_tables"] == ["GSE555001_DESeq2_KOvsWT.txt.gz"]
    assert geo["listing"]["samples"] == 12
    assert geo["bioaf"]["retrievable"] == "yes"
    assert geo["bioaf"]["analyzable"] == "yes"


def test_a_deposit_past_the_lookup_bound_is_listed_as_not_looked_up():
    [geo] = _inventory(
        extracted_accessions=["GSE5"],
        deposits=[{"accession": "GSE5", "archive": "geo", "looked_up": False, "failure_reason": "Not looked up"}],
    )
    assert geo["bioaf"]["retrievable"] == "unknown"
    assert geo["looked_up"] is False


def test_resources_link_to_the_experiments_that_name_them():
    resources = _inventory(
        scanned=[{"identifier": "GSE555001", "archive": "geo"}, {"identifier": "PXD099001", "archive": "pride"}],
        experiments=[
            {"id": "e1", "assay": "bulk RNA-seq", "resources": ["GSE555001"]},
            {"id": "e2", "assay": "mass spectrometry", "resources": ["PXD099001"]},
        ],
    )
    by_id = {r["identifier"]: r for r in resources}
    assert by_id["GSE555001"]["reported_experiment_ids"] == ["e1"]
    assert by_id["GSE555001"]["linked_by"] == "paper"
    assert by_id["PXD099001"]["reported_experiment_ids"] == ["e2"]


def test_repository_metadata_links_a_deposit_the_paper_did_not_place():
    resources = _inventory(
        scanned=[{"identifier": "GSE555001", "archive": "geo"}],
        deposits=[{"accession": "GSE555001", "archive": "geo", "exists": "yes", "listing": {"library_strategies": ["ChIP-Seq"]}}],
        experiments=[
            {"id": "e1", "assay": "ChIP-seq", "workflow": "nf-core/chipseq", "resources": []},
            {"id": "e2", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq", "resources": []},
        ],
    )
    assert resources[0]["reported_experiment_ids"] == ["e1"]
    assert resources[0]["linked_by"] == "repository"


def test_an_unlinked_resource_stays_listed_and_says_so():
    [pdb] = _inventory(scanned=[{"identifier": "7ABC", "archive": "pdb"}], experiments=[{"id": "e1", "assay": "ChIP-seq"}])
    assert pdb["reported_experiment_ids"] == []
    assert pdb["linked_by"] is None


def test_the_report_carries_one_row_per_resource():
    resources = _inventory(
        model_resources=[{"identifier": "PXD099001", "type": "proteomics_data", "role": "the proteome"}],
        scanned=[{"identifier": "GSE555001", "archive": "geo"}],
    )
    summary = summarize(study={"state": "classified"}, evidence={}, plan={"resources": resources}, targets=[], issues=[])
    rows = {row["identifier"]: row for row in summary["resources"]}
    assert rows["PXD099001"]["type_label"] == "Proteomics data"
    assert rows["PXD099001"]["retrievable_label"] == "No"
    assert "no PRIDE adapter" in rows["PXD099001"]["limitation"]
    assert rows["GSE555001"]["type_label"] == "Sequencing data"
