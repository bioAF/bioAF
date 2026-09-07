"""Ask for help when a deposited result table's columns are not in the alias list.

`result_set_normalizer._pick` matches a squashed header cell against an enumerated list of spellings,
so it only recognises what somebody wrote down. A real csaw deposit names its coordinate columns
`regions.seqnames` / `regions.start` / `regions.end`, none of which is enumerated, and the table
parsed to zero entities under "could not locate chrom/start/end columns". csaw and DiffBind both
prefix their columns, so that is a family of deposits rather than one odd file.

This is the same defect plan_6 fixed for CLAIMS, one layer over: a paper's arbitrary vocabulary
mapped onto ours by a lookup table that fails on anything unlisted. So it gets the same seam. In
`autonomous` the model is asked; in `assisted` the header is handed to a person at the C1 gate.

**Only the header row is sent.** The model never sees the table, does not parse it, and does not
decide what the numbers mean: it answers which column plays which role, and deterministic code does
the rest. One line of text per call.
"""

from __future__ import annotations

import logging

from app.services.llm_decision import confidence_of, decide, fenced_json

logger = logging.getLogger("bioaf.column_resolution")

# The step in the user's language, for the issues section of the report.
COLUMN_RESOLUTION_INTENT = "reading the columns of the paper's result table"

# What each table kind needs before it can be normalized. `pval` is a fallback for `padj`, so it is
# offered but never required.
ROLES: dict[str, tuple[str, ...]] = {
    "interval": ("chrom", "start", "end", "lfc", "padj", "pval"),
    "gene": ("id", "lfc", "padj", "pval"),
    # plan_7 step 7. A deposited SAMPLE METADATA table is the third kind whose headers are the
    # depositor's own vocabulary, and the bioinformaticians named the failure explicitly: "other
    # times the metadata has it, but the headers may be incorrect". Same seam, one more kind.
    "sample_metadata": ("sample_id", "condition", "replicate", "batch"),
}

_ROLE_HELP = {
    "chrom": "the chromosome or sequence name",
    "start": "the interval start coordinate",
    "end": "the interval end coordinate",
    "id": "the gene identifier (symbol, Ensembl or Entrez)",
    "lfc": "the log2 fold change, signed, giving the direction of the change",
    "padj": "the ADJUSTED p-value / FDR / q-value",
    "pval": "the nominal p-value, only if there is no adjusted one",
    "sample_id": "the sample identifier, matching the matrix's column names",
    "condition": "the experimental group this sample belongs to (treated/control, genotype, ...)",
    "replicate": "the replicate number within a condition",
    "batch": "the batch, run or lane, if one is recorded",
}


def build_column_prompt(header: list[str], *, kind: str) -> tuple[str, str]:
    """Return (system, payload) asking which column of this header plays which role."""
    roles = ROLES.get(kind, ROLES["interval"])
    lines = "\n".join(f"- {r}: {_ROLE_HELP[r]}" for r in roles)
    system = (
        "You are identifying the columns of a differential-analysis result table deposited alongside "
        "a paper. You are given ONLY the header row. Do not infer anything about the data.\n\n"
        f"Say which column name plays each of these roles:\n{lines}\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"columns": {"role": "exact column name from the header"}, "reason": "one sentence", '
        '"confidence": 0.0 to 1.0}\n\n'
        "Rules:\n"
        "- Use the column names EXACTLY as they appear in the header, including any prefix.\n"
        "- Omit a role rather than guess. A wrong column is worse than a missing one, because the "
        "table is then read as saying something it does not.\n"
        "- Where a table carries several statistics side by side (a combined test and a per-window "
        "best test, say), choose the primary one the analysis is reported on, and prefer the "
        "ADJUSTED significance over the nominal p-value.\n"
        "- Do not invent a column that is not in the header."
    )
    payload = "Header row of the deposited table:\n\n" + "\n".join(f"  {h}" for h in header)
    return system, payload


def parse_column_resolution(response_text: str, *, header: list[str]) -> dict:
    """Read the mapping, keeping only roles that name a column the header actually has.

    A model that invents a column would produce a map that silently blanks the table, which is the
    failure this whole path exists to remove.
    """
    empty = {"columns": {}, "reason": "", "confidence": 0.0}
    data = fenced_json(response_text)
    if data is None:
        return empty

    reason = str(data.get("reason") or "").strip()
    raw = data.get("columns") if isinstance(data.get("columns"), dict) else {}
    columns, invented = {}, []
    for role, name in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if name in header:
            columns[str(role)] = name
        else:
            invented.append(name)
    if invented:
        reason = f"{reason} (ignored {', '.join(invented)}: not in the header)".strip()

    return {"columns": columns, "reason": reason, "confidence": confidence_of(data.get("confidence"))}


async def resolve_columns(
    header: list[str], *, kind: str, client, model: str, api_key: str | None, on_issue=None
) -> dict | None:
    """Which column plays which role, or None when there is nothing to ask or the ask failed.

    Best-effort by design: a provider outage leaves the table exactly as unparsed as it already was,
    and the gate still reports the header for a person.
    """
    if not header:
        return None
    system, payload = build_column_prompt(header, kind=kind)
    # `allowed` is the header, because the closed set here is the VALUES: a column name the table
    # does not have would be read as a role assignment and parse the wrong column. The role KEYS are
    # this module's own closed set and it checks them itself, three lines below.
    decision = await decide(
        intent=COLUMN_RESOLUTION_INTENT,
        system=system,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        allowed=header,
    )
    if not decision.ok:
        # The table stays exactly as unparsed as it already was, and the gate still reports the
        # header for a person to resolve.
        if on_issue:
            on_issue(decision.as_issue(impact="degraded"))
        return None

    resolved = parse_column_resolution(decision.text, header=header)
    if not resolved["columns"]:
        return None
    return {**resolved, "model": model}
