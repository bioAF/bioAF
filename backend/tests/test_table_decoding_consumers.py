"""plan_8_2 section 1.2 acceptance: every consumer of a table reads the same bytes the same way.

Acquisition, supplement inspection, supplement consistency, the queued consistency worker and the input
previews each decoded tables on their own. The same UTF-16 file was binary to one of them and NUL-laden
UTF-8 to another. Each consumer is given the same payloads here and must agree on whether it is a
readable table, and on what its header says.
"""

import bz2
import gzip
import io

import pytest

from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.deposit_acquisition import UnreadableDepositError, decode_deposit
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.supplement_inventory import RESULTS_TABLE, classify_supplement, measure_table
from app.services.validation_author_consistency import claim_predicates, supplement_consistency
from app.services.validation_input_choice import preview_file
from app.services.validation_study_service import ValidationStudyService

_HEADER = ["gene", "log2FoldChange(treated/control)", "pvalue", "padj"]
_TEXT = "\t".join(_HEADER) + "\n" + "".join(f"u{i}\t2.0\t0.001\t0.01\n" for i in range(3)) + "n1\t0.1\t0.5\t0.9\n"


def _xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for line in _TEXT.splitlines():
        ws.append(line.split("\t"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


READABLE = {
    "utf-8": _TEXT.encode("utf-8"),
    "utf-8 with a mark": b"\xef\xbb\xbf" + _TEXT.encode("utf-8"),
    "utf-16 little-endian": b"\xff\xfe" + _TEXT.encode("utf-16-le"),
    "utf-16 big-endian": b"\xfe\xff" + _TEXT.encode("utf-16-be"),
    "utf-32 little-endian": b"\xff\xfe\x00\x00" + _TEXT.encode("utf-32-le"),
    "gzip utf-16": gzip.compress(b"\xff\xfe" + _TEXT.encode("utf-16-le")),
    "bzip2 text": bz2.compress(_TEXT.encode("utf-8")),
}
UNREADABLE = {
    "binary": b"\x89PNG\r\n\x1a\n" + bytes(range(256)),
    "malformed utf-16": b"\xff\xfe" + _TEXT.encode("utf-16-le") + b"\x00\xd8",
    "latin-1 without a mark": "gène\tlog2FoldChange\n".encode("latin-1"),
    "a NUL in the text": _TEXT.encode("utf-8").replace(b"u1", b"u\x001"),
}


@pytest.mark.parametrize("label", list(READABLE))
def test_a_readable_table_reads_the_same_everywhere(label):
    blob = READABLE[label]
    text, _fmt = decode_deposit("t.txt", blob)
    assert text.splitlines()[0].split("\t") == _HEADER
    assert classify_supplement("t.txt", blob) == RESULTS_TABLE
    assert measure_table(blob)["columns"] == _HEADER


@pytest.mark.parametrize("label", list(UNREADABLE))
def test_an_unreadable_payload_is_refused_everywhere_with_the_same_reason(label):
    blob = UNREADABLE[label]
    with pytest.raises(UnreadableDepositError) as refused:
        decode_deposit("t.txt", blob)
    assert "t.txt arrived but could not be interpreted" in str(refused.value)
    assert classify_supplement("t.txt", blob) != RESULTS_TABLE
    assert measure_table(blob) == {}


def test_an_excel_results_table_reads_the_same_everywhere():
    blob = _xlsx()
    text, fmt = decode_deposit("t.xlsx", blob)
    assert fmt == "xlsx" and text.splitlines()[0].split("\t") == _HEADER


_CONTRAST = {"name": "treated vs control", "test_condition": "treated", "reference_condition": "control"}
_CLAIM = {
    "claim_text": "3 genes up",
    "claimed_value": 3,
    "direction": "up",
    "output_type": "gene_set_size",
    "contrast_index": 0,
    "cutoffs": [{"kind": "pvalue", "operator": "<", "value": 0.01}],
    "count_relation": "=",
}


class _Plan:
    differential_design_json = {"contrasts": [_CONTRAST]}


@pytest.mark.parametrize("label", list(READABLE))
def test_supplement_consistency_reads_a_readable_table(label):
    predicates = claim_predicates([dict(_CLAIM)], _Plan())
    records = supplement_consistency(READABLE[label], "t.txt", predicates)
    assert [r["outcome"] for r in records] == ["agree"]


@pytest.mark.parametrize("label", list(UNREADABLE))
def test_supplement_consistency_says_an_unreadable_table_arrived_and_was_not_interpreted(label):
    predicates = claim_predicates([dict(_CLAIM)], _Plan())
    records = supplement_consistency(UNREADABLE[label], "t.txt", predicates)
    assert [r["outcome"] for r in records] == ["unresolved"]
    assert "t.txt arrived but could not be interpreted" in records[0]["reason"]
    assert records[0]["decoding"]["status"] == "unresolved"


@pytest.mark.parametrize("label", ["utf-16 little-endian", "gzip utf-16", "utf-8 with a mark"])
@pytest.mark.asyncio
async def test_a_preview_reads_the_header_whatever_the_encoding(label):
    blob = READABLE[label]

    async def stream(url, max_bytes):
        return blob[:max_bytes]

    preview = await preview_file("https://x/t.txt", "t.txt", stream=stream)
    assert preview["header"] == _HEADER and preview["error"] is None


@pytest.mark.asyncio
async def test_a_preview_of_an_undecodable_file_says_so_rather_than_showing_replacement_characters():
    # A lone low surrogate mid-stream is invalid in any prefix; a trailing incomplete character is not.
    blob = b"\xff\xfe" + "gene\t".encode("utf-16-le") + b"\x00\xdc" + _TEXT.encode("utf-16-le")

    async def stream(url, max_bytes):
        return blob

    preview = await preview_file("https://x/t.txt", "t.txt", stream=stream)
    assert preview["header"] == [] and "not valid UTF-16" in preview["error"]


_ACCESSION = "GSE777001"
_TABLE = "GSE777001_results.txt.gz"
_URL = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE777nnn/GSE777001/suppl/" + _TABLE


async def _seed(session, user):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=[_ACCESSION],
        differential_design={"contrasts": [{**_CONTRAST, "reported_experiment_id": "e1"}]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0]}],
        resources=[{"identifier": _ACCESSION, "type": "sequencing_data", "reported_experiment_ids": ["e1"]}],
    )
    await ReproductionPlanService.add_comparison_targets(
        session, plan, [{**_CLAIM, "metric_key": "", "reported_experiment_id": "e1"}]
    )
    study.evidence_json = {
        "capabilities": {"deposits": [{"accession": _ACCESSION, "archive": "geo", "result_tables": [_TABLE]}]}
    }
    await session.flush()
    return study, plan


@pytest.mark.parametrize("label", ["gzip utf-16", "utf-32 little-endian"])
@pytest.mark.asyncio
async def test_the_queued_check_reads_a_wide_encoding_as_acquisition_does(session, admin_user, label):
    study, plan = await _seed(session, admin_user)
    await consistency.enqueue(session, study, plan)

    async def fetch(url):
        return READABLE[label]

    await consistency.run_pending(session, study, plan, fetcher=fetch)
    (record,) = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
    assert record.outcome_json["outcome"] == "agree"
    assert record.outcome_json["columns"]["lfc"] == "log2FoldChange(treated/control)"
    assert record.attempts_json[-1]["decoding"]["encoding"] in ("utf-16-le", "utf-32-le")


@pytest.mark.asyncio
async def test_the_queued_check_records_a_table_it_could_not_interpret_and_never_stores_a_nul(session, admin_user):
    study, plan = await _seed(session, admin_user)
    await consistency.enqueue(session, study, plan)

    async def fetch(url):
        return gzip.compress(_TEXT.encode("utf-16-le"))  # UTF-16 with its mark missing

    await consistency.run_pending(session, study, plan, fetcher=fetch)
    await session.flush()
    (record,) = await queue.records_for(session, study.id, kind=queue.AUTHOR_RESULTS)
    assert record.state == queue.UNRESOLVED
    assert "arrived but could not be interpreted" in record.outcome_json["reason"]
    assert "\x00" not in str(record.outcome_json) and "\x00" not in str(record.attempts_json)
