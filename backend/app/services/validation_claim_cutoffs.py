"""change_7.3 section 7: cutoffs belong to claims, and a contrast takes its threshold from its finding.

A ComparisonTarget had one scalar ``threshold`` and no link to a contrast, so "padj < 0.05 and
|log2FC| > 2" could not be stored at all. The extractor gave each contrast ONE threshold pair, and that
pair normalizes the ground truth. A paper that reports 194 significant genes and then 88 of them above
a two-fold change makes two claims on one contrast at two cutoffs; a contrast carrying the stricter
pair reproduces the subset and scores the headline claim against it.

**Two claims with different cutoffs stay two claims.** A contrast never merges them. It takes its
threshold from the claim that is its finding: the one the extraction names, or else the linked
set-size claim with the fewest cutoffs, which is the set the others were refined from.
"""

from __future__ import annotations

_KINDS = {
    "padj": "padj",
    "fdr": "padj",
    "q": "padj",
    "qvalue": "padj",
    "adj.p.val": "padj",
    "adjusted_p": "padj",
    "pvalue": "pvalue",
    "p": "pvalue",
    "abs_log2fc": "abs_log2fc",
    "log2fc": "abs_log2fc",
    "abs_logfc": "abs_log2fc",
    "fold_change": "fold_change",
}
_OPERATORS = ("<", "<=", ">", ">=")
# The direction a bare scalar threshold means for each kind.
_DEFAULT_OPERATOR = {"padj": "<", "pvalue": "<", "abs_log2fc": ">", "fold_change": ">"}
_SET_OUTPUTS = ("gene_set_size", "region_set_size", "peak_set_size")


def _cutoff(kind, operator, value) -> dict | None:
    canonical = _KINDS.get(str(kind or "").strip().lower())
    if canonical is None:
        return None
    op = str(operator or _DEFAULT_OPERATOR.get(canonical, "")).strip()
    if op not in _OPERATORS:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return {"kind": canonical, "operator": op, "value": number}


def claim_cutoffs(claim: dict) -> list[dict]:
    """A claim's cutoffs as ``[{"kind", "operator", "value"}]``. An unusable entry is dropped, never
    guessed; a scalar ``threshold`` with its kind becomes one cutoff."""
    found: list[dict] = []
    for raw in claim.get("cutoffs") or []:
        if isinstance(raw, dict):
            cutoff = _cutoff(raw.get("kind"), raw.get("operator"), raw.get("value"))
            if cutoff is not None and cutoff not in found:
                found.append(cutoff)
    if not found and claim.get("threshold") is not None:
        cutoff = _cutoff(claim.get("threshold_kind"), None, claim.get("threshold"))
        if cutoff is not None:
            found.append(cutoff)
    return found


def contrast_index_for(claim: dict, contrasts: list[dict]) -> int | None:
    """Which contrast a claim reports on: by index, or by the contrast's name."""
    ref = claim.get("contrast")
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref if 0 <= ref < len(contrasts) else None
    wanted = " ".join(str(ref or "").split()).lower()
    if not wanted:
        return None
    for index, contrast in enumerate(contrasts):
        if " ".join(str(contrast.get("name") or "").split()).lower() == wanted:
            return index
    return None


def _is_set_claim(claim: dict) -> bool:
    return str(claim.get("output_type") or "").lower() in _SET_OUTPUTS


def derive_contrast_thresholds(contrasts: list[dict], claims: list[dict]) -> list[dict]:
    """Each contrast's threshold pair, taken from the claim that is its finding. Returns the contrasts.

    A contrast with no linked set claim, or whose finding claim states no cutoff, keeps the pair the
    extraction gave it. ``thresholds_from_claim`` records which claim the pair came from, so a reader
    can see it was derived rather than stated.
    """
    for index, contrast in enumerate(contrasts):
        linked = [
            (position, claim)
            for position, claim in enumerate(claims)
            if contrast_index_for(claim, contrasts) == index and claim_cutoffs(claim)
        ]
        named = contrast.get("finding_claim_index")
        finding = next(((p, c) for p, c in linked if p == named), None) if isinstance(named, int) else None
        if finding is None:
            sets = [(p, c) for p, c in linked if _is_set_claim(c)]
            finding = min(sets, key=lambda pc: len(claim_cutoffs(pc[1])), default=None)
        if finding is None:
            continue
        position, claim = finding
        cutoffs = claim_cutoffs(claim)
        contrast["thresholds"] = {
            "padj": next((c["value"] for c in cutoffs if c["kind"] == "padj"), None),
            "log2fc": next((c["value"] for c in cutoffs if c["kind"] == "abs_log2fc"), None),
        }
        contrast["thresholds_from_claim"] = position
    return contrasts
