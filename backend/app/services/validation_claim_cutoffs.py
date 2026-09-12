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
    from app.services.validation_predicate import adjustment_of

    found: list[dict] = []
    for raw in claim.get("cutoffs") or []:
        if isinstance(raw, dict):
            cutoff = _cutoff(raw.get("kind"), raw.get("operator"), raw.get("value"))
            # change_7.5 section 3.1: an adjustment method the paper states rides on its adjusted P.
            if cutoff is not None and cutoff["kind"] == "padj" and adjustment_of(raw):
                cutoff["adjustment"] = adjustment_of(raw)
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


_WORDS = {"padj": "adjusted P", "pvalue": "P", "abs_log2fc": "|log2FC|", "fold_change": "fold change"}
SIGNIFICANCE_KINDS = ("padj", "pvalue")
_SIGNIFICANCE_KINDS = SIGNIFICANCE_KINDS
_EFFECT_KINDS = ("abs_log2fc", "fold_change")


def significance_cutoff(kind, operator, value) -> dict | None:
    """A significance cutoff (a P value or an adjusted one) in canonical form, or None when it is not
    one: an FDR and a q-value are adjusted P values, and ``>`` never bounds a significance measure."""
    cutoff = _cutoff(kind, operator, value)
    if cutoff is None or cutoff["kind"] not in SIGNIFICANCE_KINDS or cutoff["operator"] not in ("<", "<="):
        return None
    return cutoff


def describe_ambiguity(ambiguity: dict) -> str:
    """change_7.5 section 1.1: a kept significance ambiguity, with each reading in its own words."""
    readings = "; or ".join(f'{describe_cutoff(r)} ("{r.get("quote")}")' for r in ambiguity.get("readings") or [])
    return (
        f"The paper supports more than one reading of this claim's significance: {readings}. Its significance "
        "is unresolved; neither reading is taken over the other."
    )


def describe_cutoff(cutoff: dict) -> str:
    """A cutoff in the reader's words: "P < 0.01", "adjusted P <= 0.1", "fold change > 3"."""
    value = cutoff.get("value")
    number = f"{value:g}" if isinstance(value, (int, float)) else str(value)
    operator = f" {cutoff.get('operator')} " if cutoff.get("operator") else " "
    return f"{_WORDS.get(cutoff.get('kind'), cutoff.get('kind'))}{operator}{number}"


def threshold_disagreement(threshold, threshold_kind, cutoffs: list[dict] | None) -> str | None:
    """Why a claim's scalar threshold and its stated cutoffs disagree, or None when they agree or
    cannot be compared.

    change_7.4 section 1.6: study 37's binding recorded ``padj 0.01`` for a claim whose cutoffs said
    ``P < 0.01``. A P value and an adjusted P value are different definitions, and neither reading is
    taken over the other: the disagreement is reported and the cutoff is unresolved.
    """
    stated = _cutoff(threshold_kind, None, threshold)
    if stated is None or not cutoffs:
        return None
    if any(c.get("kind") == stated["kind"] and c.get("value") == stated["value"] for c in cutoffs):
        return None
    listed = " and ".join(describe_cutoff(c) for c in cutoffs)
    return (
        f"The claim's threshold ({_WORDS.get(stated['kind'], stated['kind'])} {stated['value']:g}) disagrees with its "
        f"stated cutoffs ({listed}), so its statistical cutoff is unresolved; neither reading is taken over the other."
    )


def derive_contrast_thresholds(contrasts: list[dict], claims: list[dict]) -> list[dict]:
    """Each contrast's threshold pair, taken from the claim that is its finding. Returns the contrasts.

    A contrast with no linked set claim, or whose finding claim states no cutoff, keeps the pair the
    extraction gave it. ``thresholds_from_claim`` records which claim the pair came from, so a reader
    can see it was derived rather than stated.

    change_7.4 section 1.6: the finding claim's cutoffs are carried whole, every kind with its
    operator, as ``cutoffs``. Study 37's ``P < 0.01`` was dropped here because only an adjusted P and
    an absolute log2 fold change were kept. A finding claim whose scalar threshold disagrees with its
    cutoffs marks the contrast ``thresholds_unresolved`` with the reason.
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
        contrast["cutoffs"] = cutoffs
        contrast["thresholds_from_claim"] = position
        disagreement = threshold_disagreement(claim.get("threshold"), claim.get("threshold_kind"), cutoffs)
        # change_7.5 section 1.1: a significance the paper's own text reads two ways is unresolved too.
        unresolved = " ".join(r for r in (claim.get("significance_unresolved"), disagreement) if r)
        if unresolved:
            contrast["thresholds_unresolved"] = unresolved
    return contrasts


def analysis_cutoffs(contrast: dict, design: dict) -> dict:
    """The significance and effect cutoffs an analysis of this contrast applies, or why it cannot.

    Returns ``{"significance", "effect", "padj_threshold", "lfc_threshold", "refusal", "statement"}``.
    change_7.4 section 1.6: nothing here is ever filled with a default. The analysis runs only on a
    stated significance cutoff, and "no fold-change requirement" is only ever what the claim states:

    - the finding claim's cutoffs (``contrast["cutoffs"]``) are its complete statement, so a
      significance cutoff with no effect cutoff states no effect requirement;
    - a contrast's own pair uses the extraction's contract, where a cutoff that does not apply to
      the contrast is null;
    - the paper-level pair says nothing either way about a null fold change, so that is refused.

    change_7.5 section 1.2: each cutoff keeps its kind and operator. ``significance`` is
    ``{"kind": "pvalue" | "padj", "operator", "value"}``; ``effect`` is ``{"kind": "abs_log2fc",
    "operator", "value"}`` on the log2 scale, or None when the claim states no fold-change requirement.
    A raw P value is executable: every template writes it. ``padj_threshold`` and ``lfc_threshold``
    are the two values under the templates' parameter names, which apply no threshold of their own.
    The legacy pair states no operator, and is read the way it was always applied: ``<=`` and ``>=``.
    """
    unresolved = contrast.get("thresholds_unresolved")
    if unresolved:
        return _refused(str(unresolved))

    cutoffs = [c for c in contrast.get("cutoffs") or [] if isinstance(c, dict)]
    if cutoffs:
        significance = next((c for c in cutoffs if c.get("kind") in _SIGNIFICANCE_KINDS), None)
        effect = next((c for c in cutoffs if c.get("kind") in _EFFECT_KINDS), None)
        none_stated = True
    elif isinstance(contrast.get("thresholds"), dict):
        significance, effect = _legacy_pair(contrast["thresholds"])
        none_stated = True
    else:
        significance, effect = _legacy_pair((design or {}).get("thresholds") or {})
        none_stated = False

    if significance is None:
        return _refused("the comparison's significance cutoff is not stated, and bioAF does not supply one")
    if significance.get("operator") not in ("<", "<="):
        return _refused(
            f"the significance cutoff ({describe_cutoff(significance)}) does not bound a P value from above"
        )
    if effect is None and not none_stated:
        return _refused("the comparison's fold-change requirement is not stated, and bioAF does not supply one")

    import math

    if effect is None:
        applied_effect, effect_words = None, "no fold-change requirement"
    elif effect["kind"] == "fold_change":
        # Linear fold change, evaluated on the log2 scale in either direction: "more than threefold"
        # is |log2FC| > log2(3). A value at or below 1 is not a fold change anyone states.
        if not isinstance(effect.get("value"), (int, float)) or effect["value"] <= 1:
            return _refused(f"the fold-change cutoff ({describe_cutoff(effect)}) cannot be read on the log2 scale")
        applied_effect = {
            "kind": "abs_log2fc",
            "operator": effect["operator"],
            "value": math.log2(float(effect["value"])),
        }
        effect_words = describe_cutoff(effect)
    else:
        applied_effect = {"kind": "abs_log2fc", "operator": effect["operator"], "value": float(effect["value"])}
        effect_words = describe_cutoff(effect)
    applied_significance = {
        "kind": significance["kind"],
        "operator": significance["operator"],
        "value": float(significance["value"]),
    }
    return {
        "significance": applied_significance,
        "effect": applied_effect,
        "padj_threshold": applied_significance["value"],
        "lfc_threshold": applied_effect["value"] if applied_effect else 0.0,
        "refusal": None,
        "statement": f"{describe_cutoff(significance)}, {effect_words}",
    }


def _legacy_pair(pair: dict) -> tuple[dict | None, dict | None]:
    """The legacy ``{padj, log2fc}`` pair as cutoffs. It could hold only an adjusted P and an absolute
    log2 fold change, and stated no operator; it was always applied as ``<=`` and ``>=``."""
    significance = {"kind": "padj", "operator": "<=", "value": pair["padj"]} if pair.get("padj") is not None else None
    effect = (
        {"kind": "abs_log2fc", "operator": ">=", "value": pair["log2fc"]} if pair.get("log2fc") is not None else None
    )
    return significance, effect


def _refused(reason: str) -> dict:
    return {
        "significance": None,
        "effect": None,
        "padj_threshold": None,
        "lfc_threshold": None,
        "refusal": reason,
        "statement": None,
    }


def recorded_cutoffs(cutoffs: dict) -> dict:
    """The applied definition as it is recorded on a bundle or a finding claim."""
    return {"significance": cutoffs.get("significance"), "effect": cutoffs.get("effect")}


def normalizer_arguments(recorded: dict | None, *, legacy: dict | None = None) -> dict | None:
    """The keyword arguments that make a table normalizer apply ``recorded``, or None when it cannot.

    ``recorded`` is ``{"significance", "effect"}`` as :func:`recorded_cutoffs` writes it. A bundle or
    claim written before it existed carries only the legacy numbers, which were always an adjusted P at
    ``<=`` and an absolute log2 fold change at ``>=``; ``legacy`` is that pair, read the same way.
    """
    significance = (recorded or {}).get("significance")
    if isinstance(significance, dict):
        effect = (recorded or {}).get("effect")
        return {
            "padj_threshold": float(significance["value"]),
            "significance_kind": significance["kind"],
            "significance_operator": significance["operator"],
            "lfc_threshold": float(effect["value"]) if isinstance(effect, dict) else 0.0,
            "effect_operator": effect["operator"] if isinstance(effect, dict) else ">=",
        }
    legacy = legacy or {}
    if legacy.get("padj_threshold") is None or legacy.get("lfc_threshold") is None:
        return None
    return {
        "padj_threshold": float(legacy["padj_threshold"]),
        "significance_kind": "padj",
        "significance_operator": "<=",
        "lfc_threshold": float(legacy["lfc_threshold"]),
        "effect_operator": ">=",
    }


def contrast_cutoff_words(contrast: dict, design: dict | None = None) -> str | None:
    """A contrast's stated cutoff in the reader's words ("P < 0.01"), or None when it states none.

    change_7.5 section 1.2: the gate and the report showed the legacy ``{padj, log2fc}`` pair, which
    cannot hold a P value, so study 38's "P < 0.01" displayed as two nulls. A legacy pair is described
    in the same vocabulary, with the operators it was applied at.
    """
    cutoffs = [c for c in (contrast or {}).get("cutoffs") or [] if isinstance(c, dict)]
    if not cutoffs:
        pair = (contrast or {}).get("thresholds")
        if not isinstance(pair, dict):
            pair = (design or {}).get("thresholds") or {}
        # The legacy pair states no operator, so none is shown.
        cutoffs = [{**c, "operator": None} for c in _legacy_pair(pair) if c is not None]
    words = [describe_cutoff(c) for c in cutoffs if isinstance(c.get("value"), (int, float))]
    return " and ".join(words) or None


def claim_cutoff_words(claim: dict) -> str | None:
    """A claim's stated cutoffs in the reader's words, or None when it states none.

    change_7.5 section 1.2: one vocabulary. A scalar threshold states no operator, so none is shown.
    """
    cutoffs = [c for c in (claim or {}).get("cutoffs") or [] if isinstance(c, dict)]
    words = [describe_cutoff(c) for c in cutoffs if isinstance(c.get("value"), (int, float))]
    if words:
        return " and ".join(words)
    threshold = (claim or {}).get("threshold")
    if threshold is None:
        return None
    canonical = _KINDS.get(str((claim or {}).get("threshold_kind") or "").strip().lower())
    if canonical is None:
        return f"{threshold:g}" if isinstance(threshold, (int, float)) else str(threshold)
    return describe_cutoff({"kind": canonical, "value": threshold})


def resolve_analysis_thresholds(claim: dict | None, design: dict | None, contrast: dict | None) -> dict:
    """The cutoffs an analysis applies: the confirmed ground-truth set's own, else the contrast's.

    The confirmed finding claim records the cutoffs its table was normalized at, and a reproduction
    compared against that set has to apply the same ones. A claim that recorded none leaves the
    contrast's statistical definition to decide, through ``analysis_cutoffs``, which never defaults.
    """
    recorded = (claim or {}).get("cutoffs")
    if isinstance(recorded, dict) and isinstance(recorded.get("significance"), dict):
        significance, effect = recorded["significance"], recorded.get("effect")
        effect_words = describe_cutoff(effect) if isinstance(effect, dict) else "no fold-change requirement"
        return {
            "significance": significance,
            "effect": effect if isinstance(effect, dict) else None,
            "padj_threshold": float(significance["value"]),
            "lfc_threshold": float(effect["value"]) if isinstance(effect, dict) else 0.0,
            "refusal": None,
            "statement": f"{describe_cutoff(significance)}, {effect_words}",
        }
    legacy = (claim or {}).get("thresholds") or {}
    if legacy.get("padj") is not None and legacy.get("log2fc") is not None:
        significance, effect = _legacy_pair(legacy)
        significance["value"], effect["value"] = float(significance["value"]), float(effect["value"])
        return {
            "significance": significance,
            "effect": effect,
            "padj_threshold": significance["value"],
            "lfc_threshold": effect["value"],
            "refusal": None,
            "statement": f"{describe_cutoff(significance)}, {describe_cutoff(effect)}",
        }
    if contrast is None:
        return analysis_cutoffs({}, design or {})
    return analysis_cutoffs(contrast, design or {})
