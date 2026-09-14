"""change_7.5 section 3.1 (7.4 section 2.4 carried forward): the finding predicate.

One canonical statistical definition per claim, replacing the competing representations (a contrast's
`thresholds`, the design's `thresholds`, a claim's `threshold` / `threshold_kind`, its `cutoffs`, and
`finding_claim_json.thresholds`). Those legacy shapes are read through ONE adapter, `legacy_cutoffs`,
and nothing new is written in them.

```
{
  "contrast_index": 0,
  "orientation": "test_over_reference",          # as the claim states its ratio
  "direction": "up | down | either",             # relative to the reference arm
  "significance": {"kind": "pvalue | padj", "operator": "< | <=", "value": 0.01,
                   "adjustment": "BH | Bonferroni | q-value | FDR unspecified" | None} | None,
  "significance_status": "resolved | unresolved",
  "effect": {"kind": "abs_log2fc", "operator": "> | >=", "value": 1.0} | {"kind": "none"} | None,
  "count": {"relation": "= | > | >= | < | <= | approx", "value": 5000,
            "tolerance": {"kind": "absolute | relative", "value": 0.05} | None,
            "entity": "gene | region | peak", "dedup": "distinct_identifier",
            "missing": "exclude_and_report"} | None,
  "stated_as": "the paper's own sentence",
  "assumptions": ["each interpretation code made, in words"],
  "status": "resolved | unresolved | not_checkable",
  "reason": str | None,
}
```

`effect` is `{"kind": "none"}` when the claim states no fold-change requirement (a claim's cutoffs are
its complete statement) and None when it is unspecified (a legacy pair with no fold change). A linear
fold change is stored on the log2 scale in either direction.

**Applied in one function.** `row_passes` decides one row; `count_passing` counts distinct identifiers,
reports rows with a missing statistic (never read as 0 or 1) and duplicates that disagree (the count is
then a range). The same function filters author tables and reproduced tables, measures supplements and
drives the consistency route. The R templates apply no threshold, so nothing is added to them.
"""

from __future__ import annotations

import math

from app.services.validation_claim_cutoffs import (
    SIGNIFICANCE_KINDS,
    _legacy_pair,
    claim_cutoffs,
    describe_cutoff,
)

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
NOT_CHECKABLE = "not_checkable"

HOLDS = "holds"
FAILS = "fails"

RELATIONS = ("=", ">", ">=", "<", "<=", "approx")
ADJUSTMENTS = ("BH", "Bonferroni", "q-value", "FDR unspecified")
_RAW_ADJUSTMENT = {"fdr": "FDR unspecified", "q": "q-value", "qvalue": "q-value", "q-value": "q-value"}
_ENTITIES = {"gene_set_size": "gene", "region_set_size": "region", "peak_set_size": "peak"}
_RELATION_WORDS = {
    "=": "exactly",
    ">": "more than",
    ">=": "at least",
    "<": "fewer than",
    "<=": "at most",
    "approx": "about",
}


def adjustment_of(raw: dict) -> str | None:
    """The adjustment method a raw cutoff states: its own `adjustment`, or what its kind names."""
    stated = str(raw.get("adjustment") or "").strip()
    for method in ADJUSTMENTS:
        if stated.lower() == method.lower():
            return method
    return _RAW_ADJUSTMENT.get(str(raw.get("kind") or "").strip().lower())


def legacy_cutoffs(
    claim: dict | None, *, contrast: dict | None = None, design: dict | None = None, finding_claim: dict | None = None
) -> list[dict]:
    """The ONE adapter for every shape a statistical definition has been stored in, as cutoffs.

    In order: the claim's own `cutoffs` (or its scalar `threshold` / `threshold_kind`), the contrast's
    `cutoffs`, a confirmed finding claim's `thresholds`, the contrast's `thresholds` pair, and last the
    design's paper-level pair. The legacy pair states no operator; it was always applied as <= and >=.
    """
    found = claim_cutoffs(claim or {})
    if found:
        return found
    stated = [c for c in (contrast or {}).get("cutoffs") or [] if isinstance(c, dict)]
    if stated:
        return claim_cutoffs({"cutoffs": stated})
    for pair in (
        (finding_claim or {}).get("thresholds"),
        (contrast or {}).get("thresholds"),
        (design or {}).get("thresholds"),
    ):
        if isinstance(pair, dict) and any(pair.get(k) is not None for k in ("padj", "log2fc")):
            return [c for c in _legacy_pair(pair) if c is not None]
    return []


def build_predicate(
    claim: dict,
    *,
    contrast: dict | None = None,
    design: dict | None = None,
    finding_claim: dict | None = None,
    inherited: dict | None = None,
) -> dict:
    """The claim's predicate, built deterministically from the fields the extraction filled.

    plan_8_2 section 3.1 and owner decision 2: a claim that states no cutoff takes one only from
    ``inherited``, a methods sentence covering every differential test in its experiment
    (``validation_methods_cutoffs.inherited_cutoffs``), with the quote recorded. The design's paper-level
    threshold pair is never substituted for it, and no conventional threshold is supplied."""
    assumptions: list[str] = []
    cutoffs, source = _stated_cutoffs(claim, contrast, finding_claim)
    if not cutoffs and (inherited or {}).get("cutoffs"):
        cutoffs = claim_cutoffs({"cutoffs": inherited["cutoffs"]})
        source = {"kind": "methods", "quote": inherited.get("quote")}
        assumptions.append(
            f'the claim states no cutoff; the methods state "{inherited.get("quote")}", which covers every '
            "differential test in its experiment"
        )
    complete = (source or {}).get("kind") in ("claim", "methods")
    significance = next((c for c in cutoffs if c.get("kind") in SIGNIFICANCE_KINDS), None)
    if significance is not None:
        adjustment = significance.get("adjustment")
        if significance["kind"] == "padj" and adjustment is None:
            for raw in (claim or {}).get("cutoffs") or []:
                if isinstance(raw, dict) and (claim_cutoffs({"cutoffs": [raw]}) or [{}])[0].get("kind") == "padj":
                    adjustment = adjustment_of(raw)
                    break
        significance = {
            "kind": significance["kind"],
            "operator": significance["operator"],
            "value": float(significance["value"]),
            "adjustment": adjustment if significance["kind"] == "padj" else None,
        }
    unresolved = (claim or {}).get("significance_unresolved") or (contrast or {}).get("thresholds_unresolved")
    significance_status = UNRESOLVED if unresolved else RESOLVED

    effect_cutoff = next((c for c in cutoffs if c.get("kind") in ("abs_log2fc", "fold_change")), None)
    effect: dict | None
    if effect_cutoff is None:
        effect = {"kind": "none"} if complete else None
    elif effect_cutoff["kind"] == "fold_change":
        value = effect_cutoff.get("value")
        if isinstance(value, (int, float)) and value > 1:
            # plan_8_2 section 3.1: applied on the log2 scale with its operator, and the paper's own
            # expression kept beside it.
            effect = {
                "kind": "abs_log2fc",
                "operator": effect_cutoff["operator"],
                "value": math.log2(float(value)),
                "stated": {"kind": "fold_change", "operator": effect_cutoff["operator"], "value": float(value)},
            }
            assumptions.append(
                f"the stated {describe_cutoff(effect_cutoff)} is applied as |log2FC| {effect_cutoff['operator']} {math.log2(float(value)):g}"
            )
        else:
            effect = None
    else:
        effect = {"kind": "abs_log2fc", "operator": effect_cutoff["operator"], "value": float(effect_cutoff["value"])}

    direction = str((claim or {}).get("direction") or "").strip().lower()
    direction = direction if direction in ("up", "down") else "either"

    count = None
    entity = _ENTITIES.get(str((claim or {}).get("output_type") or "").lower())
    value = (claim or {}).get("claimed_value")
    if value is not None and (entity or isinstance((claim or {}).get("contrast_index"), int)):
        relation = str((claim or {}).get("count_relation") or "").strip()
        if relation not in RELATIONS:
            relation = "="
            assumptions.append("the paper states the count without a qualifier, so it is read as exactly that number")
        tolerance = (claim or {}).get("tolerance")
        count = {
            "relation": relation,
            "value": float(value),
            "tolerance": (
                {"kind": "relative" if float(tolerance) < 1 else "absolute", "value": float(tolerance)}
                if isinstance(tolerance, (int, float)) and tolerance > 0
                else None
            ),
            "entity": entity or "gene",
            "dedup": "distinct_identifier",
            "missing": "exclude_and_report",
        }

    status, reason = RESOLVED, None
    if significance_status == UNRESOLVED:
        status, reason = UNRESOLVED, str(unresolved)
    elif significance is None and count is not None:
        status, reason = NOT_CHECKABLE, _no_cutoff_reason(inherited, design)
    elif significance is not None and significance["operator"] not in ("<", "<="):
        status, reason = (
            NOT_CHECKABLE,
            f"the significance cutoff ({describe_cutoff(significance)}) does not bound a P value from above",
        )
    elif effect is None and significance is not None and not complete:
        status, reason = (
            UNRESOLVED,
            "the comparison's fold-change requirement is not stated, and bioAF does not supply one",
        )

    return {
        "contrast_index": (claim or {}).get("contrast_index"),
        "cutoff_source": source,
        "orientation": "test_over_reference",
        "direction": direction,
        "significance": significance,
        "significance_status": significance_status,
        "effect": effect,
        "count": count,
        "stated_as": (claim or {}).get("claim_text"),
        "assumptions": assumptions,
        "status": status,
        "reason": reason,
    }


def _no_cutoff_reason(inherited: dict | None, design: dict | None) -> str:
    """Why a count claim has no significance cutoff: bioAF supplies none, and why none was inherited."""
    reason = "the claim states no significance cutoff, and bioAF supplies none"
    why = (inherited or {}).get("reason")
    if why:
        return f"{reason}: {why}"
    pair = [c for c in _legacy_pair((design or {}).get("thresholds") or {}) if c is not None]
    if pair:
        words = " and ".join(describe_cutoff(c) for c in pair)
        return (
            f"{reason}; the paper-level threshold ({words}) is not stated for this claim's experiment in a methods "
            "sentence, so it is not applied"
        )
    return reason


def _derived_from_a_claim(contrast: dict | None) -> bool:
    """Whether a contrast's cutoffs were taken from one of its claims (``derive_contrast_thresholds``), so
    they are that claim's statement, not another claim's."""
    return (contrast or {}).get("thresholds_from_claim") is not None or bool(
        [c for c in (contrast or {}).get("cutoffs") or [] if isinstance(c, dict)]
    )


def _stated_cutoffs(claim: dict, contrast: dict | None, finding_claim: dict | None) -> tuple[list[dict], dict | None]:
    """The cutoffs the claim itself states, and where they are stored.

    plan_8_2 section 3.1 and owner decision 2: a claim's own statement (its cutoffs, or its scalar
    threshold). A contrast's cutoffs taken from another of its claims are that claim's, never this one's. A
    legacy per-contrast pair, from an extraction that stored a contrast's threshold there rather than on
    its claims, is still read as the claim's own, and is never a complete statement."""
    own = claim_cutoffs(claim or {})
    if own:
        return own, {"kind": "claim"}
    if _derived_from_a_claim(contrast):
        return [], None
    legacy = legacy_cutoffs({}, contrast=contrast, finding_claim=finding_claim)
    return legacy, ({"kind": "legacy_pair"} if legacy else None)


# What a predicate says, for deciding whether a check must be re-evaluated: the paper's own expression of an
# applied cutoff and where the cutoff came from are kept for the reader, and change nothing the check applies.
_PRESENTATION_KEYS = ("cutoff_source",)


def predicate_identity(predicate: dict | None) -> dict | None:
    """The predicate without what only presents it, so presenting it better never re-evaluates a check."""
    if not isinstance(predicate, dict):
        return predicate
    identity = {k: v for k, v in predicate.items() if k not in _PRESENTATION_KEYS}
    effect = identity.get("effect")
    if isinstance(effect, dict) and "stated" in effect:
        identity["effect"] = {k: v for k, v in effect.items() if k != "stated"}
    return identity


_COMPARE = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def row_passes(predicate: dict, *, pvalue: float | None, padj: float | None, lfc: float | None) -> bool | None:
    """Whether one row passes the predicate: significance by its kind and operator, the effect on the
    log2 scale, and the direction. None when a statistic the predicate needs is missing (never read as
    0 or 1)."""
    significance = predicate.get("significance")
    if significance:
        value = pvalue if significance["kind"] == "pvalue" else padj
        if value is None:
            return None
        if not _COMPARE[significance["operator"]](value, significance["value"]):
            return False
    effect = predicate.get("effect") or {"kind": "none"}
    needs_lfc = effect.get("kind") != "none" or predicate.get("direction") in ("up", "down")
    if needs_lfc and lfc is None:
        return None
    if effect.get("kind") == "abs_log2fc" and not _COMPARE[effect["operator"]](abs(lfc), effect["value"]):
        return False
    direction = predicate.get("direction")
    if direction == "up" and not lfc > 0:
        return False
    if direction == "down" and not lfc < 0:
        return False
    return True


def count_passing(rows: list[dict], predicate: dict) -> dict:
    """Count the distinct identifiers passing the predicate.

    ``rows`` are ``{"id", "pvalue", "padj", "lfc"}``. Returns ``{"count", "count_range", "passing",
    "rows_missing", "duplicates_disagreeing", "rows_tested"}``: a duplicate identifier whose rows
    disagree is counted as a range, and a row missing a needed statistic is excluded and reported.
    """
    verdicts: dict[str, set] = {}
    missing = 0
    for row in rows:
        identifier = str(row.get("id") or "").strip()
        if not identifier:
            continue
        verdict = row_passes(predicate, pvalue=row.get("pvalue"), padj=row.get("padj"), lfc=row.get("lfc"))
        if verdict is None:
            missing += 1
            continue
        verdicts.setdefault(identifier, set()).add(verdict)
    sure = sorted(i for i, v in verdicts.items() if v == {True})
    disagreeing = sorted(i for i, v in verdicts.items() if len(v) > 1)
    return {
        "count": len(sure) if not disagreeing else None,
        "count_range": [len(sure), len(sure) + len(disagreeing)],
        "passing": sure,
        "rows_missing": missing,
        "duplicates_disagreeing": disagreeing,
        "rows_tested": len(rows),
    }


def evaluate_count(count: dict | None, observed: list[int] | tuple[int, int]) -> tuple[str, str]:
    """``(holds | fails | unresolved, words)``: the claim's count relation against an observed count or
    range. A range that straddles the relation is unresolved, and "about" with no stated tolerance is
    unresolved, never compared against a guessed one."""
    if not count:
        return UNRESOLVED, "the claim states no count"
    low, high = observed
    value, relation = count["value"], count["relation"]
    claimed = f"{_RELATION_WORDS.get(relation, relation)} {value:g}"
    if relation == "approx":
        tolerance = count.get("tolerance")
        if not tolerance:
            return UNRESOLVED, f"the claim states about {value:g} with no tolerance, and bioAF does not guess one"
        allowed = tolerance["value"] * value if tolerance["kind"] == "relative" else tolerance["value"]
        verdicts = {abs(n - value) <= allowed for n in (low, high)}
    elif relation == "=":
        verdicts = {n == value for n in (low, high)}
    else:
        verdicts = {_COMPARE[relation](n, value) for n in (low, high)}
    observed_words = f"{low}" if low == high else f"between {low} and {high}"
    if verdicts == {True}:
        return HOLDS, f"{observed_words} against the claim's {claimed}"
    if verdicts == {False}:
        return FAILS, f"{observed_words} against the claim's {claimed}"
    return UNRESOLVED, f"{observed_words} straddles the claim's {claimed}"


def predicate_words(predicate: dict, *, contrast: dict | None = None) -> str:
    """The predicate in words: "KO versus WT, P < 0.01, down, no fold-change requirement"."""
    parts = []
    test, reference = (contrast or {}).get("test_condition"), (contrast or {}).get("reference_condition")
    if test and reference:
        parts.append(f"{test} versus {reference}")
    elif (contrast or {}).get("name"):
        parts.append(str(contrast["name"]))
    significance = predicate.get("significance")
    parts.append(describe_cutoff(significance) if significance else "no significance cutoff stated")
    parts.append({"up": "up", "down": "down"}.get(predicate.get("direction"), "either direction"))
    effect = predicate.get("effect")
    if effect is None:
        parts.append("fold-change requirement not stated")
    elif effect.get("kind") == "none":
        parts.append("no fold-change requirement")
    elif isinstance(effect.get("stated"), dict):
        parts.append(f"{describe_cutoff(effect['stated'])} ({describe_cutoff(effect)})")
    else:
        parts.append(describe_cutoff(effect))
    return ", ".join(parts)
