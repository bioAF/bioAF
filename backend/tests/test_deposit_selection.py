"""plan_7 step 2: which deposited file to reproduce from.

Step 1 lists what a study deposited. Choosing among that list is a scientific decision, not a
lookup, so it gets plan_6's seam: the model decides, states why, says how sure it was, and the
choice is stored with the model that made it. In `assisted` nothing is asked and the inventory goes
to a person at the C1 gate.

**Filenames, sizes and classifications only.** The model never sees file CONTENTS here; a matrix is
megabytes and the choice does not need them. What the numbers mean is settled by deterministic
measurement in step 6, which also overrules the model's `value_type` when the two disagree.
"""

import pytest

from app.services.deposit_selection import (
    build_selection_prompt,
    parse_selection,
    select_deposit,
)
from app.services.literature.deposit_inventory_service import DepositEntry


def _entry(filename, classification, size_bytes=1000, gsm=None, level="series"):
    return DepositEntry(
        filename=filename,
        url=f"https://example.invalid/{filename}",
        classification=classification,
        level=level,
        gsm=gsm,
        size_bytes=size_bytes,
    )


_INVENTORY = [
    _entry("GSE274331_TPMs_H2AS40-KD.xlsx", "matrix_normalized", 240000),
    _entry("GSE274331_RAW.tar", "raw", 835317760),
    _entry("GSM8447562_H2AS40Gc_MM231.bigwig", "coverage", 176283816, gsm="GSM8447562", level="sample"),
    _entry("GSE274331_sample_metadata.tsv", "metadata", 4000),
]


# ---- prompt ----


def test_the_prompt_carries_the_inventory_but_never_file_contents():
    system, payload = build_selection_prompt(_INVENTORY, pipeline_key="nf-core/rnaseq", kind="gene")
    for e in _INVENTORY:
        assert e.filename in payload
    assert "matrix_normalized" in payload
    # Sizes are in the payload so the model can prefer a table over a tarball.
    assert "835317760" in payload or "835,317,760" in payload or "796" in payload
    assert "value_type" in system


def test_the_prompt_states_the_plan_context():
    system, payload = build_selection_prompt(_INVENTORY, pipeline_key="nf-core/rnaseq", kind="gene")
    assert "nf-core/rnaseq" in payload
    assert "gene" in payload


def test_the_prompt_is_empty_handed_on_an_empty_inventory():
    assert build_selection_prompt([], pipeline_key=None, kind=None) == (None, None)


# ---- parsing ----


def _fenced(body: str) -> str:
    return f"here you go\n```json\n{body}\n```\ntrailing prose"


def test_parse_selection_reads_a_well_formed_answer():
    out = parse_selection(
        _fenced(
            '{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", '
            '"metadata_file": "GSE274331_sample_metadata.tsv", '
            '"value_type": "tpm", "reason": "the only per-gene table", "confidence": 0.8}'
        ),
        inventory=_INVENTORY,
    )
    assert out["primary_matrix"] == "GSE274331_TPMs_H2AS40-KD.xlsx"
    assert out["metadata_file"] == "GSE274331_sample_metadata.tsv"
    assert out["value_type"] == "tpm"
    assert out["confidence"] == 0.8
    assert out["declined"] is False


def test_a_file_not_in_the_inventory_is_refused():
    """The same guard `parse_column_resolution` applies to an invented column. A model that names a
    file GEO does not hold would send step 5 to download a 404."""
    out = parse_selection(
        _fenced('{"primary_matrix": "GSE274331_counts_that_do_not_exist.tsv", "value_type": "counts"}'),
        inventory=_INVENTORY,
    )
    assert out["primary_matrix"] is None
    assert "not in the deposit" in out["reason"]


def test_a_metadata_file_not_in_the_inventory_is_dropped_without_losing_the_matrix():
    out = parse_selection(
        _fenced(
            '{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", "metadata_file": "invented.tsv", "value_type": "tpm"}'
        ),
        inventory=_INVENTORY,
    )
    assert out["primary_matrix"] == "GSE274331_TPMs_H2AS40-KD.xlsx"
    assert out["metadata_file"] is None


def test_an_unknown_value_type_becomes_unknown_rather_than_being_trusted():
    out = parse_selection(
        _fenced('{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", "value_type": "vibes"}'),
        inventory=_INVENTORY,
    )
    assert out["value_type"] == "unknown"


def test_a_declined_answer_keeps_its_reason():
    out = parse_selection(
        _fenced('{"declined": true, "reason": "the deposit holds only coverage tracks and a tarball"}'),
        inventory=_INVENTORY,
    )
    assert out["declined"] is True
    assert out["primary_matrix"] is None
    assert "coverage" in out["reason"]


def test_parse_selection_survives_junk():
    for junk in ("", "no json here", "```json\nnot json\n```", "```json\n[1,2,3]\n```"):
        out = parse_selection(junk, inventory=_INVENTORY)
        assert out["primary_matrix"] is None
        assert out["confidence"] == 0.0


def test_confidence_is_clamped():
    out = parse_selection(
        _fenced('{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", "value_type": "tpm", "confidence": 45}'),
        inventory=_INVENTORY,
    )
    assert out["confidence"] == 1.0


def test_a_raw_tarball_can_never_be_selected():
    """`_RAW.tar` is in the inventory because it is deposited, not because it is a candidate.
    Selecting it would send step 5 to download 796 MB it cannot read."""
    out = parse_selection(
        _fenced('{"primary_matrix": "GSE274331_RAW.tar", "value_type": "counts"}'),
        inventory=_INVENTORY,
    )
    assert out["primary_matrix"] is None
    assert "raw" in out["reason"].lower()


# ---- the call ----


class _Client:
    def __init__(self, output="", raises=False):
        self.output = output
        self.raises = raises
        self.calls: list[dict] = []

    async def submit(self, *, prompt, payload, model, api_key):
        self.calls.append({"prompt": prompt, "payload": payload, "model": model})
        if self.raises:
            raise RuntimeError("provider down")
        return self.output


@pytest.mark.asyncio
async def test_select_deposit_returns_the_choice_with_the_model_that_made_it():
    client = _Client(
        _fenced('{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", "value_type": "tpm", "confidence": 0.9}')
    )
    out = await select_deposit(
        _INVENTORY, pipeline_key="nf-core/rnaseq", kind="gene", client=client, model="claude-x", api_key=None
    )
    assert out["primary_matrix"] == "GSE274331_TPMs_H2AS40-KD.xlsx"
    assert out["model"] == "claude-x"
    assert out["decided_by"] == "model"


@pytest.mark.asyncio
async def test_a_provider_outage_leaves_the_deposit_unchosen_rather_than_failing_the_study():
    """Asking for help must never be able to fail a study: the gate still shows the inventory."""
    out = await select_deposit(
        _INVENTORY, pipeline_key=None, kind=None, client=_Client(raises=True), model="m", api_key=None
    )
    assert out is None


@pytest.mark.asyncio
async def test_an_empty_inventory_is_never_sent_to_the_model():
    client = _Client()
    out = await select_deposit([], pipeline_key=None, kind=None, client=client, model="m", api_key=None)
    assert out is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_inventory_with_nothing_reproducible_is_never_sent_to_the_model():
    """Coverage tracks and a tarball. There is no question to ask, and asking would invite the model
    to pick one anyway."""
    client = _Client()
    nothing = [
        _entry("GSE1_RAW.tar", "raw"),
        _entry("GSM1_x.bigwig", "coverage", gsm="GSM1", level="sample"),
    ]
    out = await select_deposit(nothing, pipeline_key=None, kind=None, client=client, model="m", api_key=None)
    assert out is None
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_declined_selection_is_returned_rather_than_swallowed():
    """A model that looked and said no is a RESULT, and the gate should show that reasoning. It is
    not the same as never having asked."""
    client = _Client(_fenced('{"declined": true, "reason": "only per-sample peaks, no matrix"}'))
    out = await select_deposit(
        [_entry("GSM1_x.narrowPeak.gz", "peaks", gsm="GSM1", level="sample")],
        pipeline_key="nf-core/atacseq",
        kind="interval",
        client=client,
        model="m",
        api_key=None,
    )
    assert out is not None
    assert out["declined"] is True
    assert "peaks" in out["reason"]


# ---- defects found by running step 2 against the real deposits (2026-09-05) ----

_PER_SAMPLE = (
    [
        _entry(f"GSM475835{i}_L2_bio{i}_act.narrowPeak.gz", "peaks", 4000000, gsm=f"GSM475835{i}", level="sample")
        for i in range(1, 4)
    ]
    + [
        _entry(f"GSM475836{i}_L2_bio{i}_con.narrowPeak.gz", "peaks", 4000000, gsm=f"GSM475836{i}", level="sample")
        for i in range(1, 4)
    ]
    + [_entry("GSE157174_RAW.tar", "raw", 50186240)]
)


def test_a_per_sample_pick_expands_to_every_sample_file():
    """Asked to choose from GSE157174's twelve per-sample narrowPeak files, the model named ONE, and
    its reasoning was right for the file it named. But a differential interval test needs every
    sample in both arms: one peak file is not a matrix, it is one column of one.

    The expansion is deterministic and is NOT asked of the model, for the same reason triplet
    grouping is not: which files form the set is answered by the classification and the GSM prefix,
    and a judgment call there would be a judgment call about nothing.
    """
    out = parse_selection(
        _fenced('{"primary_matrix": "GSM4758351_L2_bio1_act.narrowPeak.gz", "value_type": "unknown"}'),
        inventory=_PER_SAMPLE,
    )
    assert out["primary_matrix"] == "GSM4758351_L2_bio1_act.narrowPeak.gz"
    assert len(out["matrix_files"]) == 6
    assert "GSE157174_RAW.tar" not in out["matrix_files"]
    # Stable order, so a re-run builds the same matrix columns in the same order.
    assert out["matrix_files"] == sorted(out["matrix_files"])


def test_a_series_level_pick_is_a_set_of_one():
    """A single deposited matrix is already the whole input. `matrix_files` is always the set to
    read, so step 5 has one shape to handle rather than two."""
    out = parse_selection(
        _fenced('{"primary_matrix": "GSE274331_TPMs_H2AS40-KD.xlsx", "value_type": "tpm"}'),
        inventory=_INVENTORY,
    )
    assert out["matrix_files"] == ["GSE274331_TPMs_H2AS40-KD.xlsx"]


def test_expansion_does_not_mix_classifications():
    """A deposit carrying both per-sample peaks and per-sample count matrices must not be merged
    into one set: they are different measurements and cannot share a matrix."""
    mixed = _PER_SAMPLE + [
        _entry("GSM9999991_counts.tsv", "matrix_counts", 5000, gsm="GSM9999991", level="sample"),
    ]
    out = parse_selection(
        _fenced('{"primary_matrix": "GSM4758351_L2_bio1_act.narrowPeak.gz", "value_type": "unknown"}'),
        inventory=mixed,
    )
    assert "GSM9999991_counts.tsv" not in out["matrix_files"]
    assert len(out["matrix_files"]) == 6


def test_a_declined_selection_has_no_matrix_files():
    out = parse_selection(_fenced('{"declined": true, "reason": "nothing usable"}'), inventory=_PER_SAMPLE)
    assert out["matrix_files"] == []


def test_the_prompt_says_a_per_sample_pick_takes_the_whole_set():
    """The model should know naming one file selects its whole group, or it will agonise over which
    of twelve equivalent files to name."""
    system, _ = build_selection_prompt(_PER_SAMPLE, pipeline_key="nf-core/atacseq", kind="interval")
    assert "per-sample" in system.lower()
