"""plan_8_3 section 0.3: what a new or tightened check does to evidence a previous build accepted.

Stage 3 added a treatment-compatibility check and stage 5 an identity-corroboration check. Both
refuse evidence the build before them accepted, and both reached the demo, because the plan says at
length that a gate must not be weakened and says nothing about a gate that refuses correct evidence.

A refusal diff answers that. It runs the superseded rule and the current one over the SAME saved
evidence, row by row, and every input whose outcome moved is listed with its evidence and justified
one by one. The superseded rule lives here, in the diff, and nowhere else: it is the baseline, not a
fallback the application may reach.

Offline and deterministic. The committed baseline is
``tests/fixtures/refusal_diffs/<check>.json``; ``tests/test_refusal_diff.py`` recomputes it.
"""

from __future__ import annotations

import json
import pathlib

from app.services.sample_record_attributes import (
    COMPATIBLE,
    CONTRADICTED,
    NOT_STATED,
    UNRESOLVED,
    condition_match,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BASELINES = FIXTURES / "refusal_diffs"

CONDITION_CHECK = "arm_condition_compatibility"

# What "refused" means for this check: the mapping is held, and the row is why.
REFUSING = (CONTRADICTED, UNRESOLVED)


def _normalized(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def superseded_condition_status(arm: str | None, record: dict | None) -> str:
    """The rule build ``fbf20d04`` applied: the arm's condition had to OCCUR in the record's condition.

    Kept here as the baseline the current rule is diffed against. It is the defect of section 3.1:
    a repository writes the arm's fact as attributes, so the paper's phrase is absent from the record
    that states exactly what it says.
    """
    wanted, stated = _normalized(arm), _normalized((record or {}).get("condition"))
    if not wanted or not stated:
        return NOT_STATED
    return COMPATIBLE if wanted in stated else CONTRADICTED


def _samd1_rows() -> list[dict]:
    """Study 56's saved proposal: each mapped column with its arm, its cited record and the contrast."""
    bundle = json.loads((FIXTURES / "samd1" / "study_56_persisted.json").read_text(encoding="utf-8"))
    evidence = bundle["study"]["evidence_json"]
    records = [r for r in evidence.get("sample_manifest") or [] if isinstance(r, dict)]
    design = bundle["reproduction_plans"][0]["differential_design_json"] or {}
    contrasts = [c for c in design.get("contrasts") or [] if isinstance(c, dict)]
    selection = (bundle["reproduction_plans"][0].get("analysis_selection_json") or {}).get("current") or {}
    position = selection.get("contrast_index")
    contrast = (
        contrasts[position] if isinstance(position, int) and position < len(contrasts) else (contrasts or [{}])[0]
    )
    rows = []
    for row in evidence["input_choice"]["mapping"]:
        if row.get("arm") not in ("test", "reference"):
            continue
        quote = next(
            (e.get("quote") for e in row.get("evidence") or [] if e.get("source") == "sample_record"),
            None,
        )
        cited = next((r for r in records if str(r.get("geo_accession") or "") in str(quote or "")), None)
        rows.append(
            {
                "input": f"study 56 / {row['column']}",
                "arm": row["arm"],
                "condition": contrast.get("test_condition" if row["arm"] == "test" else "reference_condition"),
                "record": cited,
                "records": records,
            }
        )
    return rows


def _panahipour_rows() -> list[dict]:
    """Study 50's saved sample records under the contrast its read selected, one row per record.

    Study 50 was deleted from the demo after plan_8_3's first implementation; its records are the
    committed fixture, and this is the mapping the read produced from them.
    """
    records = json.loads((FIXTURES / "panahipour" / "sample_records.json").read_text(encoding="utf-8"))
    contrast = {"test_condition": "Mucoderm® + HA", "reference_condition": "Mucoderm®"}
    rows = []
    for record in records:
        condition = record["condition"]
        arm = (
            "test" if condition.endswith("Mucoderm® + HA") else "reference" if condition.endswith("Mucoderm®") else None
        )
        if arm is None:
            continue
        rows.append(
            {
                "input": f"study 50 / {record['title']}",
                "arm": arm,
                "condition": contrast["test_condition" if arm == "test" else "reference_condition"],
                "record": record,
                "records": records,
            }
        )
    return rows


def _driver_rows() -> list[dict]:
    """The driver's own mapping fixture: a deposit whose genotype is spelled out and whose arms are
    abbreviated, and whose other attributes hold clone, culture and time.

    It is constructed evidence rather than a study's, and it belongs in the diff because it is an
    input the deployed build accepted: it caught the attribute rule refusing "WT" for a record stating
    `genotype: wild type`, which the phrase test had accepted only by finding "wt" inside "WT1".
    """
    records = [
        {"geo_accession": "GSM1", "title": "WT_a", "condition": "genotype: wild type; culture: WT1; time: day 0"},
        {"geo_accession": "GSM2", "title": "WT_b", "condition": "genotype: wild type; culture: WT2; time: day 0"},
        {"geo_accession": "GSM3", "title": "KO_c5", "condition": "genotype: SAMD1 KO; clone: c5; time: day 0"},
        {"geo_accession": "GSM4", "title": "KO_c16", "condition": "genotype: SAMD1 KO; clone: c16; time: day 0"},
    ]
    arms = {"WT_a": "reference", "WT_b": "reference", "KO_c5": "test", "KO_c16": "test"}
    contrast = {"test_condition": "SAMD1 KO", "reference_condition": "WT"}
    return [
        {
            "input": f"driver fixture / {record['title']}",
            "arm": arms[record["title"]],
            "condition": contrast["test_condition" if arms[record["title"]] == "test" else "reference_condition"],
            "record": record,
            "records": records,
        }
        for record in records
    ]


def condition_diff() -> list[dict]:
    """Every saved mapping row, under the superseded rule and the current one."""
    diff = []
    for row in [*_samd1_rows(), *_panahipour_rows(), *_driver_rows()]:
        superseded = superseded_condition_status(row["condition"], row["record"])
        current = condition_match(row["condition"], row["record"], row["records"])
        diff.append(
            {
                "input": row["input"],
                "arm": row["arm"],
                "condition": row["condition"],
                "states": (row["record"] or {}).get("condition"),
                "deployed": superseded,
                "current": current["status"],
                "attribute": current["attribute"],
            }
        )
    return diff


def load_baseline(check: str) -> dict:
    return json.loads((BASELINES / f"{check}.json").read_text(encoding="utf-8"))


def moved(entry: dict) -> bool:
    """Whether this input's outcome moved between the two rules."""
    return (entry["deployed"] in REFUSING) != (entry["current"] in REFUSING)
