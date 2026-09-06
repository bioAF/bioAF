"""plan_7 step 2: which deposited file to reproduce from.

``deposit_inventory_service`` lists what a study deposited. Choosing among that list is a scientific
decision, not a lookup, so it gets the seam plan_6 established for claim binding and a55fe889
extended to column names: **in `autonomous` the model decides and says why; in `assisted` the
inventory goes to a person at the C1 gate.** Either way the choice is stored with its reason, its
confidence and the model that made it.

**Filenames, sizes and classifications only.** The model never sees file CONTENTS. A deposited
matrix is megabytes and the choice does not need them: what is being asked is "which of these files
is the paper's processed result", which the names, sizes and types answer. What the numbers inside
actually ARE is settled by deterministic measurement in step 6, and that measurement OVERRULES the
``value_type`` claimed here. A file named ``counts.tsv`` holding floats that sum to 1e6 per column
is a CPM table whatever the model or the depositor called it.

Nothing here can fail a study. A provider outage returns None and the gate falls back to showing the
inventory to a person, which is exactly the assisted path.
"""

from __future__ import annotations

import json
import logging
import re

from app.services.literature.deposit_inventory_service import DepositEntry

logger = logging.getLogger("bioaf.deposit_selection")

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

# What the matrix holds. Drives step 8's choice of statistical test, which is why "unknown" is a
# value rather than an omission: DESeq2 on a normalized matrix is wrong, and a route that cannot
# tell the difference must say so rather than default to counts.
VALUE_TYPES = ("counts", "tpm", "fpkm", "cpm", "normalized_other", "log_transformed", "unknown")

# Classifications that can be a reproduction input. Everything else is deposited but not a
# candidate: `raw` is a tarball of reads, `coverage` is a bigwig with no per-feature values, and a
# `de_table`/`da_table` is the paper's ANSWER, which is ground truth rather than something to
# recompute from (using it as the input would be scoring the paper against itself).
_SELECTABLE = ("matrix_counts", "matrix_normalized", "peaks", "barcodes", "features")


def _size_hint(n: int | None) -> str:
    if n is None:
        return "size unknown"
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n} bytes (~{n / div:.1f} {unit})"
    return f"{n} bytes"


def selectable(inventory: list[DepositEntry]) -> list[DepositEntry]:
    """The entries that could be a reproduction input, in inventory order."""
    return [e for e in inventory or [] if e.classification in _SELECTABLE]


def build_selection_prompt(
    inventory: list[DepositEntry], *, pipeline_key: str | None, kind: str | None
) -> tuple[str | None, str | None]:
    """Return (system, payload) asking which deposited file to reproduce from, or (None, None) when
    there is nothing to ask."""
    if not inventory:
        return None, None

    system = (
        "You are choosing which file from a public GEO deposit to reproduce a paper's finding from. "
        "You are given ONLY a file listing: names, sizes and a rough classification. You cannot see "
        "the contents of any file, and you must not pretend to.\n\n"
        "Choose:\n"
        "- primary_matrix: the file holding per-feature values for every sample (a counts, TPM or "
        "expression matrix, or a per-sample peak/quantification file). This is what the "
        "differential analysis will run on.\n"
        "- metadata_file: a file describing the SAMPLES (condition, group, replicate, batch), if "
        "one is present. Choose one even when the matrix looks self-describing: its column names "
        "may not be recoverable on their own.\n"
        "- value_type: what you expect the matrix to hold, one of "
        f"{', '.join(VALUE_TYPES)}. State your expectation from the name; it will be verified "
        "against the file itself and your answer overruled if it disagrees.\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"primary_matrix": "exact filename", "metadata_file": "exact filename or null", '
        '"value_type": "one of the listed values", "reason": "one sentence", '
        '"confidence": 0.0 to 1.0, "declined": false}\n\n'
        "Rules:\n"
        "- Use filenames EXACTLY as listed. Do not invent a file.\n"
        "- When a deposit holds one per-sample file per GSM (per-sample peaks or matrices), name "
        "any ONE of them: the whole group is taken as the input automatically. Do not try to pick "
        "the best single sample, and do not decline because there is no combined matrix.\n"
        "- Never choose a raw archive (a .tar of reads) or a coverage track (bigwig/bedgraph): "
        "neither carries per-feature values that a differential test can read.\n"
        "- Never choose the paper's own differential result table as the matrix. That is the answer "
        "being checked, not the input to recompute from.\n"
        '- If nothing in the deposit can serve, set "declined": true and say why in one sentence. '
        "Declining is a real answer and is preferred to a bad choice, because the alternative route "
        "re-runs the whole pipeline from raw reads at real cost."
    )

    lines = []
    for e in inventory:
        where = f"sample {e.gsm}" if e.gsm else "series"
        lines.append(f"  {e.filename}\n      classification={e.classification}  {where}  {_size_hint(e.size_bytes)}")
    context = []
    if pipeline_key:
        context.append(f"The paper's assay maps to {pipeline_key}.")
    if kind:
        context.append(f"The finding to reproduce is a '{kind}' finding.")
    payload = (
        ("\n".join(context) + "\n\n" if context else "") + "Files deposited with this study:\n\n" + "\n".join(lines)
    )
    return system, payload


def parse_selection(response_text: str, *, inventory: list[DepositEntry]) -> dict:
    """Read the choice, keeping only files the deposit actually holds.

    Two guards, and both exist because the consequence lands on a download rather than on a parse:
    a file that is not in the inventory would send step 5 at a 404, and a `raw` or `coverage` pick
    would send it at hundreds of megabytes it cannot read.
    """
    empty = {
        "primary_matrix": None,
        "matrix_files": [],
        "metadata_file": None,
        "value_type": "unknown",
        "reason": "",
        "confidence": 0.0,
        "declined": False,
    }

    match = _FENCED_JSON_RE.search(response_text or "")
    if not match:
        return empty
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty

    by_name = {e.filename: e for e in inventory or []}
    reason = str(data.get("reason") or "").strip()
    notes: list[str] = []

    def _resolve(key: str, *, must_be_selectable: bool) -> str | None:
        name = data.get(key)
        if not isinstance(name, str) or not name.strip():
            return None
        entry = by_name.get(name.strip())
        if entry is None:
            notes.append(f"ignored {name.strip()}: not in the deposit")
            return None
        if must_be_selectable and entry.classification not in _SELECTABLE:
            notes.append(f"ignored {entry.filename}: classified {entry.classification}, not a reproducible input")
            return None
        return entry.filename

    primary = _resolve("primary_matrix", must_be_selectable=True)
    metadata = _resolve("metadata_file", must_be_selectable=False)

    # A per-sample deposit is ONE input made of many files, so naming one of them selects its whole
    # group. Found by running this against GSE157174: asked to choose among twelve per-sample
    # narrowPeak files the model named one, and its reasoning about that file was right, but one
    # peak file is a single column rather than a matrix and a differential test needs both arms.
    #
    # Deterministic, and deliberately not asked of the model, for the same reason triplet grouping
    # is not: the classification and the GSM prefix already answer which files form the set, so
    # there is no judgment to make. Grouped on classification so that a deposit carrying per-sample
    # peaks AND per-sample count matrices cannot merge two different measurements into one matrix.
    matrix_files: list[str] = []
    if primary:
        chosen = by_name[primary]
        if chosen.level == "sample":
            matrix_files = sorted(
                e.filename for e in inventory or [] if e.level == "sample" and e.classification == chosen.classification
            )
        else:
            matrix_files = [primary]

    value_type = str(data.get("value_type") or "").strip().lower()
    if value_type not in VALUE_TYPES:
        value_type = "unknown"

    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0

    if notes:
        reason = f"{reason} ({'; '.join(notes)})".strip()

    return {
        "primary_matrix": primary,
        "matrix_files": matrix_files,
        "metadata_file": metadata,
        "value_type": value_type,
        "reason": reason,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "declined": bool(data.get("declined")),
    }


async def select_deposit(
    inventory: list[DepositEntry],
    *,
    pipeline_key: str | None,
    kind: str | None,
    client,
    model: str,
    api_key: str | None,
) -> dict | None:
    """Which deposited file to reproduce from, or None when there is nothing to ask or the ask failed.

    Returns None when the deposit holds nothing reproducible: there is no question, and asking would
    invite a choice among files that cannot serve. A model that WAS asked and declined returns a
    result carrying `declined` and its reasoning, because "looked and said no" is a finding the gate
    should show, and it is not the same as never having looked.
    """
    if not selectable(inventory):
        return None

    system, payload = build_selection_prompt(inventory, pipeline_key=pipeline_key, kind=kind)
    if not system:
        return None

    try:
        output = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - asking for help must not be able to fail a study
        logger.warning("deposit selection failed (falling back to the assisted pick): %s", exc)
        return None

    chosen = parse_selection(output, inventory=inventory)
    if not chosen["primary_matrix"] and not chosen["declined"]:
        return None
    return {**chosen, "model": model, "decided_by": "model"}
