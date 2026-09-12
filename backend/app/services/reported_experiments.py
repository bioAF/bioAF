"""change_7.5 section 2.2: the experiments a paper reports, each with its own assay.

A Reported Experiment (glossary) is one experiment a Paper reports: an assay applied to stated
conditions, with its own reference and deposited data. It is not an Experiment, the lab entity; code
names it ``reported_experiment`` for that reason.

Study 38's paper ran ChIP-seq and bulk RNA-seq, and the extraction held one paper-level ``method`` for
both. The mapper answered the first route it met, and every RNA-seq claim was then refused. Workflow
selection has to operate on one experiment and one claim, so the reading describes each experiment
separately and every claim and contrast is linked to exactly one of them.

**Validation is deterministic, and a violation is a blocker, never a guess:**

- every claim and every contrast belongs to exactly one experiment;
- every experiment names one assay;
- one experiment naming two assays is a blocker (a two-assay description is two experiments).

**A plan written before this** has no experiments. It is read as one ``legacy_unverified`` experiment
built from its contrasts, never treated as a newly verified one. A legacy plan whose contrasts span
two assays is ambiguous, and a resumed execution then needs a renewed selection first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEGACY_UNVERIFIED = "legacy_unverified"
EXTRACTED = "extracted"


@dataclass
class ExperimentReading:
    experiments: list[dict] = field(default_factory=list)
    claim_experiment: dict[int, str] = field(default_factory=dict)
    contrast_experiment: dict[int, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)


def _text(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _indices(raw, limit: int) -> list[int]:
    out: list[int] = []
    for value in raw if isinstance(raw, list) else []:
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if 0 <= value < limit and value not in out:
            out.append(value)
    return out


def _strings(raw) -> list[str]:
    return [t for t in (_text(v) for v in (raw if isinstance(raw, list) else [])) if t]


def _reference_part(raw: dict, name: str) -> dict:
    """One part of a stated reference, as the paper words it, with the quote it rests on."""
    return {
        "stated": _text(raw.get(name)),
        "quote": _text(raw.get(f"{name}_quote")),
        "resolved": None,
        "status": None,
    }


def assay_is_compound(assay: str | None) -> bool:
    """Whether an assay string names two route families (``ChIP-seq and bulk RNA-seq``)."""
    from app.services.pipeline_mapper import pipelines_named_by, same_route_family

    named = pipelines_named_by(assay)
    return any(not same_route_family(a, b) for i, a in enumerate(named) for b in named[i + 1 :])


def normalize_reported_experiments(raw, *, claim_count: int, contrast_count: int) -> ExperimentReading:
    """The reading's experiments, validated. Never raises; every violation is a blocker."""
    reading = ExperimentReading()
    seen_ids: set[str] = set()
    claim_owners: dict[int, list[str]] = {}
    contrast_owners: dict[int, list[str]] = {}

    for position, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        experiment_id = _text(item.get("id"))
        if not experiment_id or experiment_id in seen_ids:
            experiment_id = next(f"e{n}" for n in range(position + 1, position + 1000) if f"e{n}" not in seen_ids)
        seen_ids.add(experiment_id)

        assay = _text(item.get("assay"))
        reference = item.get("reference") if isinstance(item.get("reference"), dict) else {}
        experiment = {
            "id": experiment_id,
            "assay": assay,
            "assay_ambiguous": bool(assay) and assay_is_compound(assay),
            "description": _text(item.get("description")),
            "conditions": _strings(item.get("conditions")),
            "time_points": _strings(item.get("time_points")),
            "organism": _text(item.get("organism")),
            "tools": _strings(item.get("tools")),
            "reference": {
                "assembly": _reference_part(reference, "assembly"),
                "annotation": _reference_part(reference, "annotation"),
            },
            "claim_indices": _indices(item.get("claim_indices"), claim_count),
            "contrast_indices": _indices(item.get("contrast_indices"), contrast_count),
            # What the paper says this experiment's data are deposited under. Linked to the resource
            # inventory by identifier (section 2.1).
            "resources": _strings(item.get("resources")),
            "status": EXTRACTED,
        }
        if not assay:
            reading.blockers.append(f"Experiment {experiment_id} names no assay, so no workflow can be chosen for it.")
        elif experiment["assay_ambiguous"]:
            reading.blockers.append(
                f"Experiment {experiment_id} names more than one assay ({assay}). A two-assay description is two "
                "experiments; this one is not analyzed until the reading separates them."
            )
        for index in experiment["claim_indices"]:
            claim_owners.setdefault(index, []).append(experiment_id)
        for index in experiment["contrast_indices"]:
            contrast_owners.setdefault(index, []).append(experiment_id)
        reading.experiments.append(experiment)

    for kind, count, owners, mapping in (
        ("claim", claim_count, claim_owners, reading.claim_experiment),
        ("contrast", contrast_count, contrast_owners, reading.contrast_experiment),
    ):
        for index in range(count):
            found = owners.get(index) or []
            if len(found) == 1:
                mapping[index] = found[0]
            elif found:
                reading.blockers.append(
                    f"The reading places {kind} {index} in more than one experiment ({', '.join(found)}), so it is "
                    "linked to none of them."
                )
            elif reading.experiments:
                reading.blockers.append(f"The reading places {kind} {index} in no experiment.")
    return reading


def _legacy_experiment(plan) -> dict:
    """The one experiment a plan written before experiments existed is read as."""
    from app.services.pipeline_mapper import pipelines_named_by, same_route_family

    contrasts = [c for c in ((plan.differential_design_json or {}).get("contrasts") or []) if isinstance(c, dict)]
    named: list[str] = []
    for contrast in contrasts:
        named.extend(pipelines_named_by(contrast.get("assay")))
    ambiguous = any(not same_route_family(a, b) for i, a in enumerate(named) for b in named[i + 1 :])
    assays = list(dict.fromkeys(c.get("assay") for c in contrasts if c.get("assay")))
    return {
        "id": "legacy",
        "assay": assays[0] if len(assays) == 1 else None,
        "assay_ambiguous": ambiguous,
        "description": None,
        "conditions": [],
        "time_points": [],
        "organism": None,
        "tools": list(getattr(plan, "tools_json", None) or []),
        "reference": {
            "assembly": {
                "stated": getattr(plan, "reference_build", None),
                "quote": None,
                "resolved": None,
                "status": None,
            },
            "annotation": {"stated": None, "quote": None, "resolved": None, "status": None},
        },
        "claim_indices": [],
        "contrast_indices": list(range(len(contrasts))),
        "resources": [],
        "status": LEGACY_UNVERIFIED,
    }


def experiments_for_plan(plan) -> list[dict]:
    """The plan's experiments, reading a legacy plan as one ``legacy_unverified`` experiment."""
    stored = getattr(plan, "reported_experiments_json", None)
    if stored:
        return [e for e in stored if isinstance(e, dict)]
    return [_legacy_experiment(plan)]


def legacy_needs_renewed_selection(plan) -> bool:
    """Whether a legacy plan's ambiguity has to be settled by a renewed selection before it resumes.

    A plan with its own experiments never does. A legacy plan does when its contrasts span two assays
    and no selection has been made since.
    """
    if getattr(plan, "reported_experiments_json", None):
        return False
    selection = getattr(plan, "analysis_selection_json", None) or {}
    if (selection.get("current") or {}).get("revision"):
        return False
    return bool(_legacy_experiment(plan)["assay_ambiguous"])


def experiment_by_id(plan, experiment_id: str | None) -> dict | None:
    return next((e for e in experiments_for_plan(plan) if e.get("id") == experiment_id), None)
