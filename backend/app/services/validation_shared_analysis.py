"""plan_8_3 stage 7.1: one valid fit supplies every comparison it can validly make.

Execution and scoring centred on a single ``level3.claim_index``. One approved analysis therefore
produced one claim comparison, however many of the finding's required claims its own output could
answer, so a finding whose claims share a contrast stayed unassessed after the work that could have
finished it had already been done and paid for.

Study 50's F1 and F4 rest on the same contrast at two different cutoffs: raw P < 0.01 with an
absolute log2 fold change of 1.5, and adjusted P < 0.05 with 1. The templates export ``gene_id``,
``log2FoldChange``, ``pvalue`` and ``padj`` UNFILTERED, so each claim's own predicate is applied to
the stored statistics rather than to a list already thresholded for somebody else's claim.

**Compatibility is checked before reuse, not after.** A claim on another contrast, from another
statistical method, whose binding failed, or needing a statistic the export does not carry, is a
different analysis and stays one. Reuse is valid only where the underlying statistical definitions
are the same question asked at a different cutoff.

**Each claim's comparison is its own record.** They share an immutable reference to the analysis that
produced them, and a later selection adds to them rather than overwriting what an earlier one
established.
"""

from __future__ import annotations

# What the current templates export for every gene, unfiltered. A bundle that does not record its own
# statistics is read as this: the three headless templates (bulk DESeq2, pseudobulk DESeq2 and
# normalized limma) all write it, and the contract is the reason a second predicate can be applied at
# all.
TEMPLATE_STATISTICS = ("gene_id", "log2FoldChange", "pvalue", "padj")

# Which exported column each kind of significance cutoff needs, and how to say it is not there.
_STATISTIC_FOR = {"padj": ("padj", "an adjusted P value"), "pvalue": ("pvalue", "a raw P value")}

# What a claim's comparison actually IS. A claimed total is a count; a claimed list of genes is an
# overlap. One fit can supply both, and one can never stand in for the other.
OPERATION_COUNT = "count"
OPERATION_NAMED = "named_entities"


def _statistics(level3: dict) -> tuple[str, ...]:
    recorded = level3.get("statistics")
    if isinstance(recorded, (list, tuple)) and recorded:
        return tuple(str(s) for s in recorded)
    return TEMPLATE_STATISTICS


def compatible_with(level3: dict, predicate: dict | None) -> tuple[bool, str | None]:
    """Whether the stored output of ``level3`` can answer ``predicate``, and why not when it cannot."""
    if not isinstance(predicate, dict) or not predicate.get("significance"):
        return False, "the claim states no statistical definition bioAF can apply to this analysis's output"
    kind = str((predicate.get("significance") or {}).get("kind") or "")
    column, words = _STATISTIC_FOR.get(kind, (None, kind or "a significance statistic"))
    if column is None:
        return False, f"this analysis's output carries no {words}"
    if column not in _statistics(level3):
        return False, f"this analysis's output carries no {words}, so the claim's cutoff cannot be applied to it"
    thresholded = level3.get("thresholded_at")
    if isinstance(thresholded, dict) and thresholded.get("significance"):
        # A retained output already filtered for one claim cannot answer a stricter one: the genes the
        # first filter removed are gone, and the second count would be wrong and look right.
        return (
            False,
            "this analysis's output was already filtered at another claim's cutoff, so a different cutoff "
            "cannot be applied to it",
        )
    return True, None


def shared_claims(
    level3: dict,
    *,
    targets: list[dict],
    contrasts: list[dict],
    include_refused: bool = False,
) -> list[dict]:
    """Every claim this fit may supply a comparison for, the selected one first.

    A claim qualifies when it is measured on the same experiment and the same contrast, its binding
    holds, its statistical definition is one this output can answer, and the analysis that produced
    the output is the method the claim needs. ``include_refused`` also returns the claims that do not
    qualify, each with the reason, so the report can say what one run left outstanding and why.
    """
    selected = level3.get("claim_index")
    contrast_name = level3.get("contrast")
    contrast_index = next(
        (i for i, c in enumerate(contrasts or []) if (c or {}).get("name") == contrast_name),
        None,
    )
    method = level3.get("method")
    rows = []
    for index, target in enumerate(targets or []):
        if not isinstance(target, dict):
            continue
        claim_index = target.get("claim_index") if isinstance(target.get("claim_index"), int) else index
        reason = None
        if contrast_index is not None and target.get("contrast_index") != contrast_index:
            reason = "it is measured on another contrast, which is a different analysis"
        elif target.get("binding_failed"):
            reason = "its binding did not complete, so what it measures is not established"
        elif target.get("method") and method and target["method"] != method:
            reason = f"it needs a {target['method']} result, and this analysis ran {method}"
        else:
            ok, why = compatible_with(level3, target.get("predicate"))
            if not ok:
                reason = why
        row = {
            "claim_index": claim_index,
            "supplied": reason is None,
            "selected": claim_index == selected,
            "predicate": target.get("predicate"),
            "operation": OPERATION_NAMED if target.get("kind") == "named_entities" else OPERATION_COUNT,
            "reason": reason,
        }
        if reason is None or include_refused:
            rows.append(row)
    rows.sort(key=lambda r: (0 if r["selected"] else 1, r["claim_index"]))
    return rows


def record_results(
    level3: dict,
    results: dict[int, dict],
    *,
    analysis_reference: str | None,
    previous: dict | None = None,
) -> dict:
    """The comparisons one analysis supplied, keyed by claim, added to what earlier analyses supplied.

    An analysis reference is immutable and per claim, so a later selection on another contrast adds
    its own claims' comparisons without overwriting what an earlier one established.
    """
    per_claim = dict((previous or {}).get("per_claim") or {})
    for index, result in (results or {}).items():
        per_claim[str(index)] = {
            **(result or {}),
            "analysis_reference": analysis_reference,
            "contrast": level3.get("contrast"),
            "method": level3.get("method"),
            "source": level3.get("source"),
        }
    return {
        "per_claim": per_claim,
        "analysis_reference": analysis_reference,
        "contrast": level3.get("contrast"),
        "method": level3.get("method"),
    }
