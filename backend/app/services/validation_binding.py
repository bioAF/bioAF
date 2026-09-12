"""change_7.5 section 2.4: bind a claim to a QC metric only when the claim measures what bioAF computes.

Study 38 bound a single ChIP-seq sample's "significant peaks" and an L3MBTL3 antibody's peaks to the
same `peak_count`, which bioAF computes as the mean over every IP sample in a run. The binding had the
claim's text, value, unit and locator and nothing else, and nothing said what the metric computes.

**Binding runs with context**: the claim's passage, the legend of the figure or table its locator
names, the methods paragraphs for its experiment's assay, the reported experiment, the descriptions of
that experiment's resources, and the claim's cutoffs. Every item is bounded, per claim and in total.

**The binding keeps three facts**, each with the quote it rests on: the measured population (and
whether it covers every sample, a subset or one sample), the aggregation, and for a proportion its
denominator.

**Bind only on a match.** Deterministic code compares the facts with what the metric declares it
computes (`MetricSpec.population`, `aggregation`, `denominator`). A mismatch, or a fact the paper does
not state, never binds: the QC check is then unavailable or unresolved, with the reason.
"""

from __future__ import annotations

# How a paper's number is aggregated. `not_stated` is the answer when the paper does not say.
AGGREGATIONS = ("per_sample", "per_group", "merged_replicates", "consensus", "per_experiment", "not_stated")
# Which samples the number covers.
SCOPES = ("all_samples", "subset", "one_sample", "not_stated")
# What a proportion is a fraction of. `none` is a count, which has no denominator.
# `trimmed_reads` is what an aligner receives after trimming, which is what an aligner's own mapping
# rate is of; `bases` is what a GC content is of.
DENOMINATORS = (
    "sequenced_reads",
    "trimmed_reads",
    "mapped_reads",
    "bases",
    "cells",
    "barcodes",
    "reads_in_cells",
    "samples",
    "none",
    "other",
)

# How each metric's own aggregation matches a paper's. A metric computed as the mean over every
# sample (or every raw file) in a run measures what a paper reports per sample across all of its
# samples; a run-level count measures what a paper reports for the experiment as a whole.
# `first_sample` (the single-cell cell and depth keys) matches nothing: it is one sample's value, and
# which sample is only known once the run lists them.
_MATCHING_AGGREGATION = {
    "mean_over_samples": "per_sample",
    "mean_over_files": "per_sample",
    "median_over_samples": "per_sample",
    "count_over_run": "per_experiment",
    "sum_over_samples": "per_experiment",
}
_AGGREGATION_WORDS = {
    "mean_over_samples": "the mean over",
    "mean_over_files": "the mean over",
    "median_over_samples": "the median over",
    "count_over_run": "a count over",
    "sum_over_samples": "the sum over",
    "first_sample": "the value for",
}

MAX_CONTEXT_CHARS = 4000
_CAPS = {"passage": 900, "legend": 600, "methods": 1400, "experiment": 400, "resources": 600, "cutoffs": 100}
MAX_TOTAL_CONTEXT_CHARS = 60_000


def _clip(text: str | None, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _legend_key(locator: str | None) -> str | None:
    """The caption a locator names: "Fig. 1G" is figure 1, "Table S2" is table s2."""
    import re

    match = re.search(r"\b(fig(?:ure)?|table)\.?\s*(S?\d+)", locator or "", re.IGNORECASE)
    if not match:
        return None
    kind = "table" if match.group(1).lower().startswith("t") else "figure"
    return f"{kind} {match.group(2).lower()}"


def _assay_words(assay: str | None) -> list[str]:
    """Words that mark a methods paragraph as belonging to an assay ("ChIP-seq" -> "chip")."""
    import re

    words = [w for w in re.split(r"[^a-z0-9]+", (assay or "").lower()) if len(w) >= 3 and w not in ("seq", "bulk")]
    return words


def binding_context(
    claim: dict,
    *,
    passage: str | None,
    sections: dict | None,
    experiment: dict | None,
    resources: list[dict] | None,
) -> dict:
    """What one claim is bound with, bounded per item and in total."""
    from app.services.validation_claim_cutoffs import claim_cutoff_words

    sections = sections or {}
    legend_key = _legend_key(claim.get("source_locator"))
    legend = (sections.get("captions") or {}).get(legend_key) if legend_key else None

    methods: list[str] = []
    words = _assay_words((experiment or {}).get("assay"))
    budget = _CAPS["methods"]
    for paragraph in sections.get("methods") or []:
        lowered = paragraph.lower()
        if words and not any(w in lowered for w in words):
            continue
        clipped = _clip(paragraph, budget)
        if not clipped:
            break
        methods.append(clipped)
        budget -= len(clipped)
        if budget <= 0:
            break

    experiment_text = ""
    if experiment:
        reference = experiment.get("reference") or {}
        parts = [
            f"{experiment.get('id')}: {experiment.get('assay') or 'assay not stated'}",
            f"conditions {', '.join(experiment.get('conditions') or []) or 'not stated'}",
            f"reference assembly {((reference.get('assembly') or {}).get('stated')) or 'not stated'}",
            f"annotation {((reference.get('annotation') or {}).get('stated')) or 'not stated'}",
        ]
        experiment_text = _clip("; ".join(parts), _CAPS["experiment"])

    linked = [
        r
        for r in resources or []
        if not experiment or experiment.get("id") in (r.get("reported_experiment_ids") or [])
    ]
    resource_lines = []
    for resource in linked:
        listing = resource.get("listing") or {}
        kinds = ", ".join(f"{k} {n}" for k, n in sorted((listing.get("kinds") or {}).items()))
        digest = f"; files: {kinds}" if kinds else ""
        samples = f"; {listing.get('samples')} samples" if listing.get("samples") else ""
        resource_lines.append(
            f"{resource.get('identifier')} ({resource.get('type')}): {resource.get('role') or 'role not stated'}{digest}{samples}"
        )

    context = {
        "passage": _clip(passage, _CAPS["passage"]),
        "legend": _clip(legend, _CAPS["legend"]) if legend else "",
        "methods": methods,
        "experiment": experiment_text,
        "resources": _clip(" | ".join(resource_lines), _CAPS["resources"]),
        "cutoffs": _clip(claim_cutoff_words(claim) or "", _CAPS["cutoffs"]),
    }
    # The caps add up to under the total, but a claim's own caption can be long: hold the total.
    while sum(len(str(v)) for v in context.values()) > MAX_CONTEXT_CHARS and context["methods"]:
        context["methods"].pop()
    return context


def _fact(raw, *, vocabulary: tuple[str, ...] | None = None, default: str | None = None) -> dict:
    raw = raw if isinstance(raw, dict) else {"value": raw}
    value = raw.get("value")
    value = " ".join(str(value).split()) if value not in (None, "") else None
    if vocabulary is not None:
        value = value if value in vocabulary else default
    return {"value": value, "quote": (" ".join(str(raw.get("quote") or "").split()) or None)}


def parse_binding_facts(decision: dict) -> dict:
    """Population, aggregation and denominator from one binding decision, each with its quote."""
    population = _fact(decision.get("population"))
    scope = (decision.get("population") or {}).get("scope") if isinstance(decision.get("population"), dict) else None
    population["scope"] = scope if scope in SCOPES else "not_stated"
    population = {"value": population["value"], "scope": population["scope"], "quote": population["quote"]}
    return {
        "population": population,
        "aggregation": _fact(decision.get("aggregation"), vocabulary=AGGREGATIONS, default="not_stated"),
        "denominator": _fact(decision.get("denominator"), vocabulary=DENOMINATORS, default=None),
    }


def merge_binding_facts(earlier: dict | None, later: dict | None) -> dict:
    """A later reading revises a fact only where it states one: a stated value is never overwritten by a
    model's `not_stated` or silence. Reconciliation writes through this."""
    merged = {key: dict(value) for key, value in (earlier or {}).items() if isinstance(value, dict)}
    for key, value in (later or {}).items():
        if not isinstance(value, dict):
            continue
        stated = value.get("value") not in (None, "", "not_stated")
        if key == "population":
            stated = stated or value.get("scope") not in (None, "not_stated")
        if stated or key not in merged:
            merged[key] = dict(value)
    return merged


def computation_words(metric_key: str, workflow: str | None = None) -> str | None:
    """What bioAF computes for ``metric_key`` on ``workflow``, in words ("the mean over every IP sample
    in the run"), or None for a key nothing computes."""
    from app.services.validation_classifier_service import _SPEC_BY_KEY

    spec = _SPEC_BY_KEY.get(metric_key)
    if spec is None:
        return None
    population, aggregation, denominator = spec.computation(workflow)
    words = f"{_AGGREGATION_WORDS.get(aggregation, aggregation)} {population}"
    if denominator not in ("none", "other"):
        words += f", as a proportion of {denominator.replace('_', ' ')}"
    return words


def binding_match(
    metric_key: str, facts: dict | None, workflow: str | None = None
) -> tuple[bool, str | None, str | None]:
    """Whether a claim with these facts measures what ``metric_key`` computes on ``workflow``:
    ``(ok, status, reason)``.

    ``status`` is ``unavailable`` for a mismatch and ``unresolved`` for a fact the paper does not state.
    """
    from app.services.validation_classifier_service import _SPEC_BY_KEY

    spec = _SPEC_BY_KEY.get(metric_key)
    if spec is None:
        return False, "unavailable", f"{metric_key} is not a metric bioAF computes"
    spec_population, spec_aggregation, spec_denominator = spec.computation(workflow)
    facts = facts or {}
    aggregation = (facts.get("aggregation") or {}).get("value") or "not_stated"
    population = facts.get("population") or {}
    scope = population.get("scope") or "not_stated"
    denominator = (facts.get("denominator") or {}).get("value")
    computes = f"{_AGGREGATION_WORDS.get(spec_aggregation, spec_aggregation)} {spec_population}"

    if spec_aggregation == "first_sample":
        if aggregation == "per_sample" and scope == "one_sample":
            return (
                False,
                "unresolved",
                f"bioAF computes {computes}, and whether that is the sample the paper names is not "
                "established before the run lists its samples",
            )
        if aggregation == "not_stated":
            return False, "unresolved", f"the paper does not state how its number is aggregated; bioAF computes {computes}"
        return (
            False,
            "unavailable",
            f"the paper's number is {aggregation.replace('_', ' ')}"
            f"{' over ' + (population.get('value') or 'its samples') if scope != 'not_stated' else ''}; "
            f"bioAF computes {computes} only",
        )
    if aggregation == "not_stated":
        return False, "unresolved", f"the paper does not state how its number is aggregated; bioAF computes {computes}"
    wanted = _MATCHING_AGGREGATION.get(spec_aggregation)
    if aggregation != wanted:
        return (
            False,
            "unavailable",
            f"the paper's number is {aggregation.replace('_', ' ')}; bioAF computes {computes}",
        )
    if wanted == "per_sample":
        if scope == "not_stated":
            return False, "unresolved", f"the paper does not state which samples its number covers; bioAF computes {computes}"
        if scope != "all_samples":
            described = population.get("value") or ("one sample" if scope == "one_sample" else "a subset of samples")
            return (
                False,
                "unavailable",
                f"the claim describes {described} ({'one sample' if scope == 'one_sample' else 'a subset'}); "
                f"bioAF computes {computes}, and a value for one sample is not a mean over all of them",
            )
    if spec_denominator not in ("none", "other"):
        if denominator is None:
            return False, "unresolved", f"the paper does not state what its proportion is of; bioAF's is of {spec_denominator.replace('_', ' ')}"
        if denominator != spec_denominator:
            return (
                False,
                "unavailable",
                f"the paper's denominator is {denominator.replace('_', ' ')}; bioAF's is {spec_denominator.replace('_', ' ')}",
            )
    return True, None, None


def settle_binding(
    key: str | None, facts: dict | None, *, workflow: str | None, earlier: dict | None = None
) -> dict:
    """What one binding decision lands as: ``{"bound_key", "binding_facts", "aggregation", "reason"}``.

    The facts merge over any earlier reading (a stated fact is never overwritten by `not_stated`), and
    the key binds only when the merged facts match the metric's computation on ``workflow``. A key that
    does not match is kept as ``proposed_key`` with the mismatch, so a later reading that states the
    missing fact can bind it. ``reason`` is the mismatch, or None.
    """
    proposed = (earlier or {}).get("proposed_key")
    earlier = {k: v for k, v in (earlier or {}).items() if k not in ("match", "proposed_key")}
    merged = merge_binding_facts(earlier, facts)
    aggregation = (merged.get("aggregation") or {}).get("value")
    key = key or proposed
    if not key:
        return {"bound_key": None, "binding_facts": merged or None, "aggregation": aggregation, "reason": None}
    ok, status, reason = binding_match(key, merged, workflow)
    merged["match"] = {"ok": ok, "status": status, "reason": reason}
    if ok:
        return {"bound_key": key, "binding_facts": merged, "aggregation": aggregation, "reason": None}
    merged["proposed_key"] = key
    return {"bound_key": None, "binding_facts": merged, "aggregation": aggregation, "reason": reason}


def bound_contexts(contexts: list[dict]) -> list[dict]:
    """Every claim's context together held under ``MAX_TOTAL_CONTEXT_CHARS``: methods go first, from
    the largest context, then resource digests, then legends."""

    def _total() -> int:
        return sum(len(str(v)) for c in contexts for v in c.values())

    for field in ("methods", "resources", "legend"):
        while _total() > MAX_TOTAL_CONTEXT_CHARS:
            largest = max(contexts, key=lambda c: len(str(c.get(field) or "")), default=None)
            if not largest or not largest.get(field):
                break
            if field == "methods":
                largest["methods"].pop()
            else:
                largest[field] = ""
    return contexts
