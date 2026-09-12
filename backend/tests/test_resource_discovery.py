"""change_7.5 section 1.5: resource discovery with nothing dropped and nothing vacuous.

Study 38 extracted `GSE144396, PXD016781`. Study 37, the same paper, extracted `GSE144396, PXD016781,
STI20A, 6LUI, 6LUJ, 6LUK` and kept three, because `_MAX_DEPOSITS = 3` truncated silently. The model's
list alone decided what the paper named, and it read only `<body>`, where data availability rarely
sits. PRIDE was "an archive bioAF does not recognise". An empty supplement manifest never requested the
bundle, and "Processed results published: No. bioAF inspected every attachment it found" was written
when none had been inspected. ENA was asked about the first sample's experiment only, so a 55-sample
series read "ENA publishes FASTQ files for 1 of 1 run(s)".

No model call is made here.
"""

import pytest

from app.services.archive_discovery import (
    EMDB,
    FIGSHARE,
    GITHUB,
    GITLAB,
    MASSIVE,
    OTHER,
    PDB,
    PRIDE,
    ZENODO,
    classify_archive,
    describe_deposit,
    describe_geo_deposit,
)
from app.services.literature.fulltext_service import _jats_to_text
from app.services.resource_identifiers import scan_identifiers
from app.services.supplement_inventory import parse_jats_supplements
from app.services.validation_capabilities import discover_capabilities
from app.services.validation_completion import completion_for

# ---- the back matter is read ----

_JATS_WITH_BACK = (
    '<?xml version="1.0"?>'
    '<article xmlns="http://jats.nlm.nih.gov"><body><sec><title>Results</title>'
    "<p>Knockout cells lost the mark.</p></sec></body>"
    "<back>"
    '<sec sec-type="data-availability"><title>Data availability</title>'
    "<p>RNA-seq data are deposited in GEO (GSE555001). Proteomics data are in PRIDE (PXD099001).</p></sec>"
    "<ack><p>We thank the core facility.</p></ack>"
    "<ref-list><ref><mixed-citation>Smith et al. data in GSE000777.</mixed-citation></ref></ref-list>"
    "</back></article>"
)


def test_the_back_matter_is_read_beside_the_body():
    text = _jats_to_text(_JATS_WITH_BACK)
    assert "Knockout cells lost the mark" in text
    assert "GSE555001" in text
    assert "PXD099001" in text
    assert "core facility" in text


def test_the_reference_list_is_not_read_as_the_papers_own_resources():
    assert "GSE000777" not in _jats_to_text(_JATS_WITH_BACK)


# ---- identifier patterns ----


def test_every_recognised_identifier_is_found_in_the_text():
    text = (
        "Sequencing data: GEO GSE555001 and SRA PRJNA555002. Proteomics: PRIDE PXD099001 and MassIVE "
        "MSV000099003. Structures: PDB accession codes 7ABC, 7ABD and 7ABE. Maps: EMD-12345. Code: "
        "https://github.com/lab/analysis and https://gitlab.com/lab/tools. Data: "
        "https://doi.org/10.5281/zenodo.555004 and 10.6084/m9.figshare.555005."
    )
    found = {(r["identifier"], r["archive"]) for r in scan_identifiers(text)}
    assert ("GSE555001", "geo") in found
    assert ("PRJNA555002", "sra") in found
    assert ("PXD099001", PRIDE) in found
    assert ("MSV000099003", MASSIVE) in found
    assert {("7ABC", PDB), ("7ABD", PDB), ("7ABE", PDB)} <= found
    assert ("EMD-12345", EMDB) in found
    assert ("https://github.com/lab/analysis", GITHUB) in found
    assert ("https://gitlab.com/lab/tools", GITLAB) in found
    assert ("10.5281/zenodo.555004", ZENODO) in found
    assert ("10.6084/m9.figshare.555005", FIGSHARE) in found


def test_a_four_character_code_is_a_pdb_entry_only_beside_a_pdb_token():
    assert not any(r["archive"] == PDB for r in scan_identifiers("We used 2020 buffers and 1ABC cells."))
    assert [r["identifier"] for r in scan_identifiers("(PDB: 1ABC)") if r["archive"] == PDB] == ["1ABC"]


def test_a_sample_accession_is_not_a_resource_of_its_own():
    assert not any(r["identifier"] == "GSM1000" for r in scan_identifiers("sample GSM1000 of GSE1000"))


def test_each_identifier_is_listed_once_in_order_of_mention():
    found = [r["identifier"] for r in scan_identifiers("GSE20001 then PXD000001, then GSE20001 again")]
    assert found == ["GSE20001", "PXD000001"]


# ---- named archives without an adapter ----


@pytest.mark.parametrize(
    "accession, archive",
    [
        ("PXD016781", PRIDE),
        ("MSV000012345", MASSIVE),
        ("EMD-12345", EMDB),
        ("10.5281/zenodo.1234", ZENODO),
        ("10.6084/m9.figshare.1234", FIGSHARE),
        ("https://github.com/lab/code", GITHUB),
        ("https://gitlab.com/lab/code", GITLAB),
    ],
)
def test_other_archives_are_named(accession, archive):
    assert classify_archive(accession) == archive


@pytest.mark.asyncio
async def test_an_archive_without_an_adapter_is_named_not_unrecognised():
    async def _never(url):
        raise AssertionError(f"looked up {url}")

    pride = await describe_deposit("PXD016781", provenance="text_scan", scoped=False, fetcher=_never)
    assert "no adapter for PRIDE" in pride["failure_reason"]
    assert "does not recognise" not in pride["failure_reason"]

    pdb = await describe_deposit("6LUI", provenance="text_scan", scoped=False, fetcher=_never, archive=PDB)
    assert pdb["archive"] == PDB
    assert "no adapter for PDB" in pdb["failure_reason"]

    unknown = await describe_deposit("STI20A", provenance="extracted", scoped=False, fetcher=_never)
    assert unknown["archive"] == OTHER
    assert "does not recognise" in unknown["failure_reason"]


# ---- nothing is truncated ----


class _Geo:
    """Answers every GEO-shaped URL with an empty but readable record, and counts lookups."""

    def __init__(self):
        self.series: set[str] = set()

    async def __call__(self, url: str) -> str:
        for token in url.replace("?", "/").split("/"):
            if token.startswith("GSE") and "nnn" not in token:
                self.series.add(token.split("_")[0])
        if "filereport" in url:
            return "run_accession\texperiment_accession\tfastq_bytes\n"
        if url.endswith("/suppl/"):
            return "<html></html>"
        if "series_matrix" in url:
            return '!Sample_title\t"a"\n!Sample_geo_accession\t"GSM1"\n'
        raise RuntimeError(f"404 {url}")


@pytest.mark.asyncio
async def test_every_named_deposit_is_listed_and_those_past_the_bound_are_not_looked_up():
    geo = _Geo()
    accessions = [{"accession": f"GSE{n}", "provenance": "extracted"} for n in range(1, 6)]
    caps = await discover_capabilities(accessions=accessions, has_full_text=True, code_availability=[], fetcher=geo)
    listed = [d["accession"] for d in caps["deposits"]]
    assert listed == [f"GSE{n}" for n in range(1, 6)]
    assert geo.series == {"GSE1", "GSE2", "GSE3"}
    unlooked = [d for d in caps["deposits"] if d.get("looked_up") is False]
    assert [d["accession"] for d in unlooked] == ["GSE4", "GSE5"]
    assert all(d["failure_reason"] == "Not looked up" for d in unlooked)


@pytest.mark.asyncio
async def test_an_archive_that_needs_no_lookup_does_not_spend_the_bound():
    geo = _Geo()
    accessions = [
        {"accession": "PXD000001", "provenance": "extracted"},
        {"accession": "PXD000002", "provenance": "extracted"},
        {"accession": "PXD000003", "provenance": "extracted"},
        {"accession": "GSE1", "provenance": "extracted"},
    ]
    caps = await discover_capabilities(accessions=accessions, has_full_text=True, code_availability=[], fetcher=geo)
    geo_deposit = next(d for d in caps["deposits"] if d["accession"] == "GSE1")
    assert geo_deposit.get("looked_up") is not False
    assert geo.series == {"GSE1"}


# ---- the text scan is unioned with the model's list at read time ----


@pytest.mark.asyncio
async def test_a_read_lists_what_the_text_names_beside_what_the_model_named(session, admin_user, monkeypatch):
    from types import SimpleNamespace

    from app.services import validation_extraction_service as ext
    from app.services.validation_driver_service import ValidationDriverService
    from app.services.validation_study_service import ValidationStudyService

    extraction = (
        '```json\n{"accessions": ["GSE555001"], "method": {"assay": "bulk RNA-seq", "reference_build": "GRCh38"}, '
        '"claims": [], "data_availability": "deposited", "blockers": []}\n```'
    )

    async def fake_get_active(sess, org_id):
        return SimpleNamespace(provider="anthropic", model="claude-opus-4-8", api_key=None)

    class _C:
        async def submit(self, prompt, payload, model, api_key, attachments=None):
            return extraction

    monkeypatch.setattr(ext.llm_provider_config_service, "get_active", fake_get_active)
    monkeypatch.setattr(ext, "get_client", lambda p: _C())
    geo = _Geo()
    monkeypatch.setattr("app.services.literature.accession_manifest_service._http_fetch_text", geo)
    monkeypatch.setattr("app.services.literature.deposit_inventory_service._http_fetch_text", geo)

    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    await ValidationDriverService.read_and_plan(
        session,
        study,
        "Data are in GEO (GSE555001) and PRIDE (PXD099001); structures are PDB 7ABC.",
        admin_user.organization_id,
        admin_user.id,
    )
    deposits = {d["accession"]: d for d in study.evidence_json["capabilities"]["deposits"]}
    assert set(deposits) == {"GSE555001", "PXD099001", "7ABC"}
    assert deposits["PXD099001"]["archive"] == PRIDE
    assert deposits["PXD099001"]["provenance"] == "text_scan"
    assert deposits["7ABC"]["archive"] == PDB


# ---- supplements ----


def test_the_table_s1_and_data_file_s1_convention_is_named():
    xml = (
        '<article xmlns="http://jats.nlm.nih.gov"><body><p>The counts are in table S1 and data file S2; '
        "see also fig. S3.</p></body></article>"
    )
    rows = parse_jats_supplements(xml)
    labels = {r["label"]: r["kind"] for r in rows}
    assert labels.get("Supplemental Table S1") == "reference"
    assert labels.get("Supplemental Data File S2") == "reference"
    assert labels.get("Supplemental Figure S3") == "figure"


def test_a_main_table_is_not_a_supplement():
    xml = '<article xmlns="http://jats.nlm.nih.gov"><body><p>As table 1 shows.</p></body></article>'
    assert parse_jats_supplements(xml) == []


@pytest.mark.asyncio
async def test_an_empty_manifest_still_requests_the_supplementary_bundle(session, admin_user, monkeypatch):
    from app.services import validation_assessment
    from app.services.validation_study_service import ValidationStudyService

    requested = []

    async def _fetch(url):
        requested.append(url)
        raise RuntimeError("404")

    monkeypatch.setattr(validation_assessment, "deposit_bytes_fetcher", _fetch)
    study = await ValidationStudyService.create_study(session, admin_user.organization_id, admin_user.id)
    study.evidence_json = {"pmcid": "PMC1234", "supplements": []}
    await session.flush()

    await validation_assessment.run_assessment(session, study)

    assert any("PMC1234" in url for url in requested)


# ---- "Processed results published" is never vacuous ----


def test_an_empty_manifest_is_not_established_rather_than_no():
    outcome = completion_for(route="deposit", capabilities={}, supplements=[], manifest_known=True)
    assert outcome["processed_results_available"] == "not_established"
    assert outcome["processed_results_reason"] == "bioAF found no attachments to inspect"


def test_no_requires_an_inspected_attachment():
    rows = [
        {
            "label": "Table S1",
            "kind": "attachment",
            "filename": "t1.csv",
            "resolved": True,
            "role": "sample_metadata",
            "retrieval": {"status": "retrieved"},
        }
    ]
    outcome = completion_for(route="deposit", capabilities={}, supplements=rows, manifest_known=True)
    assert outcome["processed_results_available"] == "no"
    assert "inspected 1" in outcome["processed_results_reason"]


def test_a_listed_deposit_result_table_counts_as_processed_results():
    caps = {
        "deposits": [
            {
                "accession": "GSE555001",
                "archive": "geo",
                "result_tables": ["GSE555001_DESeq2_KO_vs_WT.txt.gz"],
            }
        ]
    }
    outcome = completion_for(route="deposit", capabilities=caps, supplements=[], manifest_known=True)
    assert outcome["processed_results_available"] == "yes"
    assert "GSE555001_DESeq2_KO_vs_WT.txt.gz" in outcome["processed_results_reason"]


@pytest.mark.asyncio
async def test_a_geo_deposit_records_the_result_tables_it_lists():
    listing = (
        '<html><a href="GSE555001_DeSeq2_KOvsWT.txt.gz">GSE555001_DeSeq2_KOvsWT.txt.gz</a>'
        '<a href="GSE555001_RAW.tar">GSE555001_RAW.tar</a></html>'
    )

    async def _fetch(url):
        if url.endswith("/suppl/"):
            return listing
        if "filereport" in url:
            return "run_accession\texperiment_accession\tfastq_bytes\n"
        if "series_matrix" in url:
            return '!Sample_title\t"a"\n'
        raise RuntimeError("404")

    deposit = await describe_geo_deposit("GSE555001", fetcher=_fetch)
    assert deposit["result_tables"] == ["GSE555001_DeSeq2_KOvsWT.txt.gz"]


def test_a_pmcid_alone_does_not_make_the_manifest_known():
    from app.services.validation_assessment import manifest_known

    assert manifest_known({"pmcid": "PMC1", "supplements": []}) is False
    assert manifest_known({"pmcid": "PMC1", "supplements": [{"label": "x"}]}) is True
    assert manifest_known({"pmcid": "PMC1", "retrieval_ledger": [{"outcome": "retrieved"}]}) is True


# ---- raw-read availability counts the series' runs ----

_MATRIX = "\n".join(
    [
        '!Sample_title\t"a"\t"b"\t"c"',
        '!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"',
        '!Sample_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX1"\t'
        '"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX2"\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX3"',
        '!Series_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP9"',
    ]
)
_SERIES_RUNS = (
    "run_accession\texperiment_accession\tsample_accession\tsample_title\tlibrary_strategy\tfastq_bytes\n"
    "SRR1\tSRX1\tS1\ta\tRNA-Seq\t10\nSRR2\tSRX2\tS2\tb\tRNA-Seq\t10\nSRR3\tSRX3\tS3\tc\tRNA-Seq\t10\n"
)


@pytest.mark.asyncio
async def test_raw_read_availability_counts_every_run_of_the_series():
    asked = []

    async def _fetch(url):
        if "filereport" in url:
            asked.append(url)
            return _SERIES_RUNS if "accession=SRP9" in url else "run_accession\n"
        if url.endswith("/suppl/"):
            return "<html></html>"
        if "series_matrix" in url:
            return _MATRIX
        raise RuntimeError("404")

    deposit = await describe_geo_deposit("GSE555001", fetcher=_fetch)
    assert any("accession=SRP9" in url for url in asked)
    assert "3 of 3 run(s)" in deposit["evidence_by_key"]["raw_data"]


@pytest.mark.asyncio
async def test_a_subset_query_says_it_was_a_subset():
    matrix = _MATRIX.replace('\n!Series_relation\t"SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP9"', "")

    async def _fetch(url):
        if "filereport" in url:
            return _SERIES_RUNS.split("\n")[0] + "\n" + _SERIES_RUNS.split("\n")[1] + "\n"
        if url.endswith("/suppl/"):
            return "<html></html>"
        if "series_matrix" in url:
            return matrix
        raise RuntimeError("404")

    deposit = await describe_geo_deposit("GSE555001", fetcher=_fetch)
    assert "only the first sample's experiment" in deposit["evidence_by_key"]["raw_data"]
