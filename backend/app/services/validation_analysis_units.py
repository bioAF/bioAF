"""plan_8_7 stage 1: the analysis units an obligation is assessed over, established before its outcome.

The owner's September 21 assessment:

    "Per-analysis allocation exists but production does not use it."

`validation_rubric_v3.allocate` has taken a ``units`` input since plan_8_4, and `build_assessment`
never passed one. Every obligation therefore carried one paper-wide verdict, so a ChIP-seq defect
settled the RNA-seq arm and one script that would not parse settled every other script.

The repair is not the weights. plan_8_7: "Splitting weights while copying the same paper-wide verdict
into every unit is not implementation." So a unit exists here only where bioAF holds evidence that
SEPARATES it:

- **an experiment** is a unit for the obligations about experiments and their analyses, because the
  reading already describes each reported experiment on its own, with its assay, conditions and tools.
  Those words are what scope its evidence packet, so two arms are judged on different passages.
- **an implementation** is a unit for the code obligations, because each supplied source is parsed on
  its own. Running one script establishes nothing about the others.
- **everything else stays paper-wide, explicitly.** M5 asks what the paper disclosed, which is one
  question about one paper.

Units are established BEFORE any outcome is known, from the plan and the held evidence alone, and the
scope carries a revision so a change to it invalidates the assessments that depended on it rather
than silently rescoring them.

Pure: no model, no database, no I/O.
"""

from __future__ import annotations

import hashlib
import json

# The obligations about one experiment and the analysis it received, and ONLY the ones bioAF asks
# about each arm separately. plan_8_7: "Splitting weights while copying the same paper-wide verdict
# into every unit is not implementation." The measured checks of the S and M sections (the organism
# every experiment states, the reference releases bioAF resolved, the sample accounting) are computed
# over the paper's whole reading in one pass and have no per-arm evidence behind them today, so they
# keep their paper-wide scope rather than being split into copies of one answer.
EXPERIMENT_SCOPED = (
    "S2.A",
    "S2.B",
    "S5.A",
    "E1.A",
    "E1.B",
    "E2.A",
    "E2.B",
    "E3.A",
    "E3.B",
    "M1.A",
    "M1.B",
    "M3.A",
    "M3.B",
)
# The obligations about one supplied implementation.
CODE_SCOPED = ("C1.A", "C1.B", "C2.A", "C2.B", "C3.A", "C3.B", "C4.A", "C4.B", "C5.A", "C5.B")
# The obligations that are about the paper, not about one analysis in it. M5 asks what was disclosed
# and where; there is one answer for one paper and splitting it would invent arms it does not have.
PAPER_WIDE_LEAVES = ("M5.A", "M5.B")
PAPER_WIDE = "the paper as a whole"

# What one obligation may be split into. plan_8_7 stage 0 declares the aggregate model budget; units
# share it, so a paper with many arms keeps its paper-wide scope and records that bioAF declined to
# split rather than multiplying every request by the number of arms.
MAX_UNITS_PER_OBLIGATION = 4
EXPERIMENT = "experiment"
IMPLEMENTATION = "implementation"


def unit_of(leaf_id: str) -> tuple[str, str | None]:
    """``("C1.A#code:a.py") -> ("C1.A", "code:a.py")``. The allocation's own spelling, read back."""
    leaf, _, unit = str(leaf_id).partition("#")
    return leaf, unit or None


def _terms(*values) -> list[str]:
    found: list[str] = []
    for value in values:
        for part in value if isinstance(value, list) else [value]:
            text = " ".join(str(part or "").split())
            if text and text not in found:
                found.append(text)
    return found


def _experiment_units(plan: dict) -> tuple[dict[str, dict], list[str]]:
    """One unit per reported experiment, with the words that scope its evidence. Unresolved ids named."""
    experiments = [e for e in plan.get("reported_experiments") or [] if isinstance(e, dict)]
    units: dict[str, dict] = {}
    unresolved: list[str] = []
    for position, experiment in enumerate(experiments, start=1):
        identity = " ".join(str(experiment.get("id") or "").split())
        assay = " ".join(str(experiment.get("assay") or "").split())
        if not identity:
            # An arm bioAF could not identify is still an arm: it holds its own allocation open and
            # takes nothing from the ones it could identify.
            identity = f"unidentified-{position}"
            unresolved.append(f"exp:{identity}")
        unit_id = f"exp:{identity}"
        if unit_id in units:
            continue
        description = " ".join(str(experiment.get("description") or "").split())
        units[unit_id] = {
            "kind": EXPERIMENT,
            "id": unit_id,
            "label": " - ".join(part for part in (assay or "an unnamed assay", description) if part),
            "assay": assay or None,
            # What an evidence packet ranks this unit's own passages by.
            "terms": _terms(assay, description, experiment.get("conditions"), experiment.get("tools")),
            "claim_indices": [i for i in experiment.get("claim_indices") or [] if isinstance(i, int)],
            "contrast_indices": [i for i in experiment.get("contrast_indices") or [] if isinstance(i, int)],
            "identified": not identity.startswith("unidentified-"),
        }
    return units, unresolved


def _code_units(evidence: dict) -> tuple[dict[str, dict], list[str]]:
    """One unit per distinct supplied implementation. The same bytes twice is one implementation."""
    inspection = evidence.get("code_inspection") if isinstance(evidence.get("code_inspection"), dict) else {}
    sources = [s for s in (inspection or {}).get("sources") or [] if isinstance(s, dict) and not s.get("generated")]
    units: dict[str, dict] = {}
    duplicates: list[str] = []
    seen: dict[str, str] = {}
    for source in sources:
        path = str(source.get("path") or "").strip() or "an unnamed file"
        digest = hashlib.sha256(str(source.get("text") or "").encode("utf-8")).hexdigest()
        if digest in seen:
            duplicates.append(path)
            units[seen[digest]]["paths"].append(path)
            continue
        unit_id = f"code:{path}"
        seen[digest] = unit_id
        units[unit_id] = {
            "kind": IMPLEMENTATION,
            "id": unit_id,
            "label": path,
            "paths": [path],
            "language": str(source.get("language") or "").lower() or None,
            "terms": _terms(path),
            "identified": True,
        }
    # Which paths belong to ANOTHER implementation. A packet for one unit drops those and keeps
    # everything else: the manifests, the paper's methods and bioAF's own observations are shared
    # evidence, and a unit judged without its environment specification would be judged on less.
    every_path = {path for unit in units.values() for path in unit["paths"]}
    for unit in units.values():
        unit["other_paths"] = sorted(every_path - set(unit["paths"]))
    return units, duplicates


def analysis_units(*, plan: dict | None, evidence: dict | None) -> dict:
    """The scope every obligation is assessed over, as ``allocate``'s ``units`` input and its record.

    ``{"units", "definitions", "paper_wide", "unresolved", "duplicates", "declined", "revision"}``.
    ``units`` is empty where the evidence separates nothing, which leaves every obligation paper-wide
    exactly as it was: a paper with one experiment and one script has one analysis, not one unit.
    """
    plan, evidence = plan or {}, evidence or {}
    experiments, unresolved = _experiment_units(plan)
    implementations, duplicates = _code_units(evidence)
    declined: dict[str, str] = {}
    if len(experiments) > MAX_UNITS_PER_OBLIGATION:
        declined[EXPERIMENT] = (
            f"this paper reports {len(experiments)} experiments and bioAF splits an obligation across at most "
            f"{MAX_UNITS_PER_OBLIGATION}, so the obligations about its experiments keep their paper-wide scope"
        )
        experiments = {}
    if len(implementations) > MAX_UNITS_PER_OBLIGATION:
        declined[IMPLEMENTATION] = (
            f"this paper supplies {len(implementations)} distinct implementations and bioAF splits an obligation "
            f"across at most {MAX_UNITS_PER_OBLIGATION}, so the code obligations keep their paper-wide scope"
        )
        implementations = {}
    units: dict[str, list[str]] = {}
    if len(experiments) > 1:
        for leaf in EXPERIMENT_SCOPED:
            units[leaf] = sorted(experiments)
    if len(implementations) > 1:
        for leaf in CODE_SCOPED:
            units[leaf] = sorted(implementations)
    definitions = {**(experiments if len(experiments) > 1 else {}), **(implementations if len(implementations) > 1 else {})}
    record = {
        "units": units,
        "definitions": definitions,
        "paper_wide": {leaf: PAPER_WIDE for leaf in PAPER_WIDE_LEAVES},
        "unresolved": [u for u in unresolved if u in definitions],
        "duplicates": duplicates,
        "declined": declined,
    }
    record["revision"] = hashlib.sha256(
        json.dumps({"units": units, "definitions": definitions}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return record
