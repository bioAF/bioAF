"""change_7.5 sections 3.2 and 3.3: the input is chosen from the claim and the evidence, and its samples
are interpreted from evidence.

Study 37 chose a matrix by its filename, grouped `KO Cl16` as condition "KO Cl", replicate 16, and
required day-7 samples the chosen matrix never held. The decision now sees the selected claim, its
predicate and contrast, the experiment, the repository's sample records and bounded previews of the
candidate files; a deterministic exact-identifier match needs no model; a model's proposal is accepted
only when every assignment cites evidence present in the inputs.
"""

import gzip
import json

import pytest

from app.services.literature.deposit_inventory_service import DepositEntry
from app.services.validation_input_choice import (
    PREVIEW_BYTES,
    PREVIEW_FILES,
    PREVIEW_ROWS,
    choose_input,
    deterministic_choice,
    input_candidates,
    preview_file,
    validate_mapping,
)

_RECORDS = [
    {"geo_accession": "GSM1", "title": "WT rep1", "condition": "genotype: wild type; cell line: E14; time: day 0"},
    {"geo_accession": "GSM2", "title": "WT rep2", "condition": "genotype: wild type; cell line: E14; time: day 0"},
    {"geo_accession": "GSM3", "title": "KO Cl5", "condition": "genotype: SAMD1 KO; clone: Cl5; time: day 0"},
    {"geo_accession": "GSM4", "title": "KO Cl16", "condition": "genotype: SAMD1 KO; clone: Cl16; time: day 0"},
    {"geo_accession": "GSM5", "title": "KO Cl5 d7", "condition": "genotype: SAMD1 KO; clone: Cl5; time: day 7"},
]
_CONTRAST = {"name": "KO vs WT", "test_condition": "SAMD1 KO", "reference_condition": "WT",
             "test_samples": [], "reference_samples": []}
_CLAIM = {"claim_text": "257 genes were up in KO (P < 0.01)", "claimed_value": 257, "direction": "up"}


def _entry(name, kind):
    return DepositEntry(filename=name, url=f"https://x/{name}", classification=kind, level="series", size_bytes=1000)


_ENTRIES = [
    _entry("GSE1_counts.txt.gz", "matrix_counts"),
    _entry("GSE1_normalized.txt.gz", "matrix_normalized"),
    _entry("GSE1_DESeq2_ESC.txt.gz", "de_table"),
    _entry("GSE1_signal.bw", "coverage"),
]


# ---- candidates and bounded previews ----


def test_matrices_are_candidates_tables_go_to_consistency_and_tracks_are_not_analyzable():
    found = input_candidates(_ENTRIES)
    assert [e.filename for e in found["matrices"]] == ["GSE1_counts.txt.gz", "GSE1_normalized.txt.gz"]
    assert [e.filename for e in found["tables"]] == ["GSE1_DESeq2_ESC.txt.gz"]
    assert [e.filename for e in found["not_analyzable"]] == ["GSE1_signal.bw"]


@pytest.mark.asyncio
async def test_a_preview_is_the_header_and_five_rows_streamed_and_capped():
    import random

    rng = random.Random(7)
    body = "gene\tWT rep1\tKO Cl5\n" + "\n".join(
        f"g{i}\t{rng.random():.6f}\t{rng.random():.6f}" for i in range(50_000)
    )
    compressed = gzip.compress(body.encode())
    asked = {}

    async def stream(url, max_bytes):
        asked["max_bytes"] = max_bytes
        return compressed[:max_bytes]

    preview = await preview_file("https://x/GSE1_counts.txt.gz", "GSE1_counts.txt.gz", stream=stream)
    assert preview["header"] == ["gene", "WT rep1", "KO Cl5"]
    assert len(preview["rows"]) == PREVIEW_ROWS
    assert preview["truncated"] is True
    assert asked["max_bytes"] < len(compressed)  # never the whole file
    assert len(json.dumps(preview)) <= PREVIEW_BYTES + 512


# ---- deterministic first ----


def test_one_matrix_whose_columns_resolve_both_arms_exactly_is_chosen_without_a_model():
    contrast = {**_CONTRAST, "test_samples": ["GSM3", "GSM4"], "reference_samples": ["GSM1", "GSM2"]}
    previews = [
        {"filename": "GSE1_counts.txt.gz", "header": ["gene", "GSM1", "GSM2", "GSM3", "GSM4"], "rows": []},
        {"filename": "GSE1_normalized.txt.gz", "header": ["gene", "a", "b"], "rows": []},
    ]
    choice = deterministic_choice(previews, contrast=contrast, sample_records=_RECORDS)
    assert choice["primary_matrix"] == "GSE1_counts.txt.gz"
    arms = {row["column"]: row["arm"] for row in choice["mapping"]}
    assert arms == {"GSM1": "reference", "GSM2": "reference", "GSM3": "test", "GSM4": "test"}


def test_a_title_equal_to_a_column_name_is_an_exact_identifier():
    contrast = {**_CONTRAST, "test_samples": ["GSM3", "GSM4"], "reference_samples": ["GSM1", "GSM2"]}
    previews = [{"filename": "m.txt", "header": ["id", "WT rep1", "WT rep2", "KO Cl5", "KO Cl16"], "rows": []}]
    choice = deterministic_choice(previews, contrast=contrast, sample_records=_RECORDS)
    assert choice is not None
    assert {r["column"] for r in choice["mapping"] if r["arm"] == "test"} == {"KO Cl5", "KO Cl16"}


def test_two_matrices_that_both_resolve_are_not_a_deterministic_answer():
    contrast = {**_CONTRAST, "test_samples": ["GSM3", "GSM4"], "reference_samples": ["GSM1", "GSM2"]}
    header = ["gene", "GSM1", "GSM2", "GSM3", "GSM4"]
    previews = [{"filename": "a", "header": header, "rows": []}, {"filename": "b", "header": header, "rows": []}]
    assert deterministic_choice(previews, contrast=contrast, sample_records=_RECORDS) is None


# ---- the model's proposal is validated against the evidence ----

_COLUMNS = ["WT-1", "WT-2", "KO Cl5 repl1", "KO Cl16", "KO Cl5 d7"]


def _row(column, arm, unit, *, evidence=None, group=None, time="day 0"):
    return {"column": column, "arm": arm, "biological_unit": unit, "biological_sample": f"{unit} {time}",
            "technical_group": group, "time_point": time,
            "evidence": evidence if evidence is not None else [{"source": "sample_record", "quote": "genotype: SAMD1 KO"}]}


def _mapping():
    wt = [{"source": "sample_record", "quote": "genotype: wild type"}]
    return [
        _row("WT-1", "reference", "WT culture 1", evidence=wt),
        _row("WT-2", "reference", "WT culture 2", evidence=wt),
        _row("KO Cl5 repl1", "test", "clone Cl5", evidence=[{"source": "sample_record", "quote": "clone: Cl5"}]),
        _row("KO Cl16", "test", "clone Cl16", evidence=[{"source": "sample_record", "quote": "clone: Cl16"}]),
        {"column": "KO Cl5 d7", "arm": "excluded", "evidence": [{"source": "sample_record", "quote": "time: day 7"}]},
    ]


def _validate(mapping):
    return validate_mapping(mapping, columns=_COLUMNS, sample_records=_RECORDS, texts=["KO Cl5 repl1", "WT-1"])


def test_a_proposal_that_cites_its_evidence_is_accepted():
    result = _validate(_mapping())
    assert result["status"] == "accepted", result["reasons"]


def test_an_assignment_citing_evidence_absent_from_the_inputs_is_unresolved():
    mapping = _mapping()
    mapping[2]["evidence"] = [{"source": "sample_record", "quote": "clone: Cl99"}]
    result = _validate(mapping)
    assert result["status"] == "unresolved"
    assert any("Cl99" in r for r in result["reasons"])


def test_every_column_is_assigned_or_excluded_explicitly():
    result = _validate(_mapping()[:-1])
    assert result["status"] == "unresolved"
    assert any("KO Cl5 d7" in r for r in result["reasons"])


def test_a_column_in_two_arms_is_unresolved():
    mapping = _mapping() + [_row("WT-1", "test", "WT culture 1")]
    assert _validate(mapping)["status"] == "unresolved"


def test_a_trailing_number_is_never_replicate_identity_on_its_own():
    mapping = _mapping()
    mapping[3] = _row("KO Cl16", "test", "16", evidence=[{"source": "column_name", "quote": "KO Cl16"}])
    result = _validate(mapping)
    assert result["status"] == "unresolved"
    assert any("trailing number" in r for r in result["reasons"])


def test_a_technical_group_needs_evidence_beyond_a_name_pattern():
    mapping = _mapping()
    mapping[0]["technical_group"] = "t1"
    mapping[1] = {**mapping[1], "biological_unit": "WT culture 1", "technical_group": "t1",
                  "evidence": [{"source": "column_name", "quote": "WT-1"}]}
    result = _validate(mapping)
    assert result["status"] == "unresolved"
    assert any("technical" in r for r in result["reasons"])


def test_a_technical_group_spanning_two_arms_is_rejected():
    mapping = _mapping()
    mapping[0]["technical_group"] = "t1"
    mapping[2] = {**mapping[2], "technical_group": "t1"}
    result = _validate(mapping)
    assert result["status"] == "unresolved"
    assert any("spans" in r for r in result["reasons"])


def test_a_unit_the_evidence_does_not_state_holds():
    mapping = _mapping()
    mapping[0]["biological_unit"] = None
    assert _validate(mapping)["status"] == "unresolved"


# ---- the decision: file, mapping and the authors' table together ----


class _Client:
    def __init__(self, answer):
        self.answer = answer
        self.payload = None

    async def submit(self, prompt, payload, model, api_key, attachments=None):
        self.payload = payload
        return "```json\n" + json.dumps(self.answer) + "\n```"


async def _stream(url, max_bytes):
    name = url.rsplit("/", 1)[-1]
    if "counts" in name:
        text = "gene\t" + "\t".join(_COLUMNS) + "\ng1\t1\t2\t3\t4\t5\n"
    elif "DESeq2" in name:
        text = "gene\tlog2FoldChange\tpvalue\tpadj\ng1\t1.2\t0.001\t0.01\n"
    else:
        text = "gene\ta\tb\ng1\t0.1\t0.2\n"
    return gzip.compress(text.encode())[:max_bytes]


@pytest.mark.asyncio
async def test_the_model_proposes_the_file_the_mapping_and_the_authors_table_together():
    client = _Client({"primary_matrix": "GSE1_counts.txt.gz", "mapping": _mapping(),
                      "author_table": "GSE1_DESeq2_ESC.txt.gz", "reason": "the ESC counts", "confidence": 0.8})
    choice = await choose_input(
        _ENTRIES, stream=_stream, claim=_CLAIM, predicate_words="SAMD1 KO versus WT, P < 0.01, up",
        contrast=_CONTRAST, experiment={"id": "e2", "assay": "bulk RNA-seq"}, sample_records=_RECORDS,
        client=client, model="m", api_key=None,
    )
    assert choice["primary_matrix"] == "GSE1_counts.txt.gz"
    assert choice["author_table"] == "GSE1_DESeq2_ESC.txt.gz"
    assert choice["mapping_validation"]["status"] == "accepted"
    assert choice["decided_by"] == "model"
    # The decision saw the claim, the predicate, the sample records and the previews.
    for seen in ("257 genes were up", "P < 0.01", "clone: Cl16", "KO Cl5 repl1", "log2FoldChange"):
        assert seen in client.payload
    assert len(choice["previews"]) <= PREVIEW_FILES


@pytest.mark.asyncio
async def test_a_file_the_deposit_does_not_hold_is_refused():
    client = _Client({"primary_matrix": "invented.txt", "mapping": [], "author_table": None, "reason": "r"})
    choice = await choose_input(
        _ENTRIES, stream=_stream, claim=_CLAIM, predicate_words="p", contrast=_CONTRAST, experiment={},
        sample_records=_RECORDS, client=client, model="m", api_key=None,
    )
    assert choice["primary_matrix"] is None
    assert "invented.txt" in choice["reason"]


@pytest.mark.asyncio
async def test_a_result_table_is_never_the_matrix():
    client = _Client({"primary_matrix": "GSE1_DESeq2_ESC.txt.gz", "mapping": [], "author_table": None, "reason": "r"})
    choice = await choose_input(
        _ENTRIES, stream=_stream, claim=_CLAIM, predicate_words="p", contrast=_CONTRAST, experiment={},
        sample_records=_RECORDS, client=client, model="m", api_key=None,
    )
    assert choice["primary_matrix"] is None
