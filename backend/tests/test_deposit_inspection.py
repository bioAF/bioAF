"""plan_7 step 6: check the deposited matrix is what we expect, before using it.

"Check if the pre-processed data aligns with what we expect" is one of the bioinformaticians' seven
asks. On the pipeline route MultiQC covers this; a deposited matrix arrives with no QC at all and
would otherwise go straight into a differential test.

**Pure measurement, no model.** Step 2 lets the model STATE a `value_type` from the filename; this
module MEASURES it from the numbers, and the measurement wins. A file named `counts.tsv` holding
floats whose columns sum to 1e6 is a CPM table whatever anyone called it, and handing it to DESeq2
would produce numbers that are confidently wrong.

The header shape asserted here is real, taken from `GSE274331_TPMs_H2AS40-KD.xlsx` decoded on the
demo 2026-09-05: an EMPTY first cell for the gene-id column, then six samples in two arms.
"""

from app.services.deposit_inspection import inspect_matrix

# The real GSE274331 shape: unnamed id column, six samples, two arms, Ensembl ids with versions.
_REAL_HEADER = "\tControl-KD_1\tControl-KD_2\tControl-KD_3\tH2AS40-KD_1\tH2AS40-KD_2\tH2AS40-KD_3"
_REAL_TPM = "\n".join(
    [
        _REAL_HEADER,
        "ENSG00000284949.1\t0\t0\t0\t0\t0.040592\t0.042122",
        "ENSG00000000003.15\t120.5\t118.2\t121.1\t95.4\t97.7\t96.1",
        "ENSG00000000005.6\t0\t0\t0\t0\t0\t0",
    ]
)


def test_reads_the_real_deposited_shape():
    """An unnamed first column is the id column. `result_set_normalizer` already treats an empty
    header cell as the index convention, and a deposit that leans on it is the common case, not an
    oddity."""
    out = inspect_matrix(_REAL_TPM)
    assert out["n_rows"] == 3
    assert out["n_columns"] == 6
    assert out["columns"] == [
        "Control-KD_1",
        "Control-KD_2",
        "Control-KD_3",
        "H2AS40-KD_1",
        "H2AS40-KD_2",
        "H2AS40-KD_3",
    ]
    assert out["id_column"] == ""


def test_integer_values_measure_as_counts():
    text = "gene\ts1\ts2\nA\t10\t12\nB\t100\t110\n"
    assert inspect_matrix(text)["value_type_observed"] == "counts"


def test_columns_summing_to_a_million_measure_as_cpm_or_tpm():
    """The defining property of TPM/CPM: every column sums to 1e6. This is the check that stops a
    normalized matrix reaching DESeq2."""
    text = "gene\ts1\ts2\nA\t400000.0\t600000.0\nB\t600000.0\t400000.0\n"
    assert inspect_matrix(text)["value_type_observed"] == "tpm_or_cpm"


def test_negative_values_measure_as_log_transformed():
    """Counts and TPM are non-negative. A negative means somebody already took a log."""
    text = "gene\ts1\ts2\nA\t-1.2\t3.4\nB\t2.2\t-0.5\n"
    assert inspect_matrix(text)["value_type_observed"] == "log_transformed"


def test_other_floats_measure_as_normalized_other():
    text = "gene\ts1\ts2\nA\t1.5\t2.5\nB\t3.5\t4.5\n"
    assert inspect_matrix(text)["value_type_observed"] == "normalized_other"


def test_a_filename_claiming_counts_does_not_beat_the_numbers():
    """The whole point of the step. GSE274331's table is honestly named, but plenty are not, and a
    mislabelled matrix silently invalidates DESeq2's dispersion model."""
    out = inspect_matrix(_REAL_TPM, claimed_value_type="counts")
    assert out["value_type_observed"] != "counts"
    assert out["value_type_disagrees"] is True


def test_agreement_is_recorded_too():
    out = inspect_matrix("gene\ts1\ts2\nA\t10\t12\n", claimed_value_type="counts")
    assert out["value_type_disagrees"] is False


def test_library_sizes_and_their_spread_are_measured():
    """A 40x spread across columns is a real observation about the deposit and belongs in the
    verdict, not hidden."""
    text = "gene\ts1\ts2\nA\t10\t400\nB\t10\t400\n"
    out = inspect_matrix(text)
    assert out["library_sizes"] == {"s1": 20.0, "s2": 800.0}
    assert out["library_size_ratio"] == 40.0


def test_all_zero_rows_and_columns_are_counted():
    text = "gene\ts1\ts2\nA\t0\t0\nB\t5\t0\nC\t7\t0\n"
    out = inspect_matrix(text)
    assert out["zero_row_fraction"] == 1 / 3
    assert out["zero_columns"] == ["s2"]


def test_the_id_namespace_is_detected():
    """Reuses `result_set_normalizer._detect_namespace`, so the deposit route and the ground-truth
    route agree about what an identifier IS. A namespace mismatch is what the concordance service
    refuses on, and it can only refuse if both sides were measured the same way."""
    assert inspect_matrix(_REAL_TPM)["id_namespace"] == "ensembl_gene"
    assert inspect_matrix("gene\ts1\nTP53\t5\nGAPDH\t7\n")["id_namespace"] == "symbol"


def test_sample_coverage_against_the_design_is_measured():
    """The study-13 lesson, moved before the notebook instead of after it: a reproduction whose
    samples are not in the matrix wrote nothing, completed cleanly, and was scored as a real
    comparison of zero against the paper's 5,607."""
    out = inspect_matrix(_REAL_TPM, design_samples=["Control-KD_1", "H2AS40-KD_1", "NOT_HERE"])
    assert out["design_samples_found"] == 2
    assert out["design_samples_missing"] == ["NOT_HERE"]


def test_zero_design_coverage_is_flagged_as_unusable():
    out = inspect_matrix(_REAL_TPM, design_samples=["nope_1", "nope_2"])
    assert out["design_samples_found"] == 0
    assert out["usable"] is False
    assert "none of the" in out["unusable_reason"].lower()


def test_a_matrix_matching_the_design_is_usable():
    out = inspect_matrix(_REAL_TPM, design_samples=["Control-KD_1", "H2AS40-KD_1"])
    assert out["usable"] is True
    assert out["unusable_reason"] is None


def test_a_single_column_matrix_is_not_usable_for_a_contrast():
    """One column cannot carry two arms."""
    out = inspect_matrix("gene\ts1\nA\t5\n")
    assert out["usable"] is False


def test_an_empty_or_headerless_table_is_refused_rather_than_measured():
    for junk in ("", "\n", "only_one_column\n"):
        out = inspect_matrix(junk)
        assert out["usable"] is False
        assert out["unusable_reason"]


def test_a_transposed_matrix_is_noticed():
    """Samples down the rows and genes across the top. Reading it as-is would treat three genes as
    three samples and analyse nothing."""
    text = "sample\tTP53\tGAPDH\tACTB\nWT_1\t5\t100\t80\nKO_1\t9\t102\t77\n"
    out = inspect_matrix(text, design_samples=["WT_1", "KO_1"])
    assert out["looks_transposed"] is True


def test_a_normal_matrix_is_not_called_transposed():
    out = inspect_matrix(_REAL_TPM, design_samples=["Control-KD_1", "H2AS40-KD_1"])
    assert out["looks_transposed"] is False


# ---- the driver handler ----

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.models.validation_study import ValidationStudy  # noqa: E402
from app.services.validation_driver_service import ValidationDriverService  # noqa: E402


class _FakeStorage:
    def __init__(self, files: dict):
        self.files = files

    async def read_text(self, uri, *, encoding="utf-8"):
        return self.files[uri]


@pytest_asyncio.fixture
async def inspecting_study(session, admin_user):
    study = ValidationStudy(
        organization_id=admin_user.organization_id,
        requested_by_user_id=admin_user.id,
        source_accession="GSE274331",
        state="inspecting_deposit",
        evidence_json={
            "route": "deposit",
            "deposit_selection": {"primary_matrix": "m.tsv", "matrix_files": ["m.tsv"], "value_type": "counts"},
            "deposit": {
                "files": [
                    {
                        "file_id": 1,
                        "filename": "m.tsv",
                        "storage_uri": "s3://x/m.tsv",
                        "artifact_type": "deposited_matrix",
                    }
                ]
            },
        },
    )
    session.add(study)
    await session.flush()
    return study


@pytest.mark.asyncio
async def test_inspection_lands_on_the_evidence_and_advances(session, inspecting_study):
    storage = _FakeStorage({"s3://x/m.tsv": _REAL_TPM})
    await ValidationDriverService._handle_inspecting_deposit(session, inspecting_study, storage_adapter=storage)
    insp = inspecting_study.evidence_json["deposit_inspection"]
    assert insp["n_columns"] == 6
    # A three-row EXCERPT of a 37,248-row TPM table does not sum to 1e6, so it measures as
    # normalized rather than per-million. That is the honest answer for this input: the per-million
    # test is a property of the whole matrix, and claiming tpm from three rows would be a guess.
    assert insp["value_type_observed"] == "normalized_other"
    assert inspecting_study.state == "reproducing"


@pytest.mark.asyncio
async def test_the_measurement_overrules_the_models_claim(session, inspecting_study):
    """Step 2's model said `counts` from the filename. The numbers say per-million normalized. The
    numbers win, and the disagreement is recorded rather than silently resolved."""
    storage = _FakeStorage({"s3://x/m.tsv": _REAL_TPM})
    await ValidationDriverService._handle_inspecting_deposit(session, inspecting_study, storage_adapter=storage)
    insp = inspecting_study.evidence_json["deposit_inspection"]
    assert insp["value_type_disagrees"] is True
    assert inspecting_study.evidence_json["deposit_selection"]["value_type"] == "counts"  # the claim is preserved


@pytest.mark.asyncio
async def test_an_unusable_matrix_holds_rather_than_running_the_notebook(session, inspecting_study):
    """The study-13 lesson enforced BEFORE compute: that notebook completed cleanly having written
    nothing, and its empty output was scored as a real comparison against the paper's 5,607."""
    storage = _FakeStorage({"s3://x/m.tsv": "gene\tonly_one\nA\t5\n"})
    await ValidationDriverService._handle_inspecting_deposit(session, inspecting_study, storage_adapter=storage)
    assert inspecting_study.state == "inspecting_deposit"
    assert inspecting_study.evidence_json["deposit_failed"]["reason"]
