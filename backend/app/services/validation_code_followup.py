"""plan_8_7 section 3: published code creates required follow-up work, one piece per implementation.

The owner, on 2026-09-27, made attempting the authors' code a requirement rather than a preference:

    "If the authors provide code, attempting that code is critical. Within existing authorization and
    supported execution capability, available compatible inputs trigger reproduction and comparison
    with the published result. If full reproduction is blocked, attempt a meaningful bounded
    invocation of the actual author code where possible, and report exactly what it establishes. If
    that is blocked too, identify the concrete prerequisite."

And the rule that makes it reach anybody: "A user must not need to discover an advanced toggle to
trigger work already covered by their authorization." So the follow-up is produced BY the assessment,
from what the study holds, rather than waiting behind a control somebody has to find.

What each row is:

- the **immutable source revision** it is about (the repository, the commit and the bytes' digest), so
  a recorded result is a result about one revision rather than about "the code";
- the **claimed analysis** and the **inputs** the attempt would need;
- the **action**: reproduce it, run the bounded check, or the named prerequisite that blocks both.

One row per implementation, because running one script establishes nothing about the others. A
generated stand-in is never one of them: plan_8_4 section 3.3, and it is the whole point of the
distinction.

Pure: no model, no database, no execution. Scheduling is the assessment stage's business.
"""

from __future__ import annotations

# What the follow-up asks for.
ATTEMPT_REPRODUCTION = "attempt_reproduction"
ATTEMPT_BOUNDED = "attempt_bounded_check"
NEEDS_AUTHORIZATION = "needs_authorization"
BLOCKED = "blocked"
ACTIONS = (ATTEMPT_REPRODUCTION, ATTEMPT_BOUNDED, NEEDS_AUTHORIZATION, BLOCKED)

# What is missing, where something is. One of these, never a sentence standing in for all of them: a
# missing runtime is bioAF's limitation, a missing input is a fact about the deposit, and a missing
# authorization is a fact about this request. plan_8_7 keeps the three apart for the same reason
# `validation_route_policy` does.
RUNTIME = "runtime"
INPUT = "input"
AUTHORIZATION = "authorization"
SOURCE = "source"
NEEDS_RUNTIME = RUNTIME
NEEDS_INPUT = INPUT
NEEDS_AUTHORIZATION_FOR = AUTHORIZATION

# The routes whose existing authorization already covers running the authors' code. `assessment`
# authorizes no execution, which is what makes it the default entry.
EXECUTING_ROUTES = ("deposit", "pipeline", "both")


def _runtime_for(language: str) -> str | None:
    from app.services.validation_environment_check import RUNTIMES

    return (RUNTIMES.get(language) or {}).get("runtime")


def _claimed_analysis(plan: dict) -> str:
    """What the paper says its analysis is, in its own words, so a comparison knows what it is about."""
    experiments = [e for e in (plan or {}).get("reported_experiments") or [] if isinstance(e, dict)]
    named = [
        " - ".join(
            part for part in (str(e.get("assay") or "").strip(), str(e.get("description") or "").strip()) if part
        )
        for e in experiments
    ]
    return "; ".join(part for part in named if part) or "the analysis this paper reports"


def code_followup(*, evidence: dict | None, plan: dict | None, route: str | None) -> dict:
    """``{"followups", "reason"}``: what attempting this paper's published code requires, per implementation."""
    from app.services.validation_analysis_units import analysis_units

    evidence, plan = evidence or {}, plan or {}
    inspection = evidence.get("code_inspection") if isinstance(evidence.get("code_inspection"), dict) else {}
    supplied = [s for s in (inspection or {}).get("sources") or [] if isinstance(s, dict)]
    authors = [s for s in supplied if not s.get("generated")]
    if not authors:
        reason = (
            "the only source bioAF holds for this paper was generated to stand in for the authors', and it "
            "establishes nothing about the code the paper supplied"
            if supplied
            else "bioAF holds no source code for this paper, so there is nothing of the authors' to attempt"
        )
        return {"followups": [], "reason": reason}

    resolution = evidence.get("code_resolution") if isinstance(evidence.get("code_resolution"), dict) else {}
    capabilities = evidence.get("capabilities") if isinstance(evidence.get("capabilities"), dict) else {}
    processed = str(((capabilities or {}).get("preprocessed_data") or {}).get("value") or "").lower()
    # `unknown` authorizes a bounded attempt: refusing on an unknown would hide a workable route behind
    # a discovery timeout, which is `validation_route_policy`'s rule and is the same rule here.
    inputs_available = processed in ("yes", "unknown", "")
    authorized = str(route or "") in EXECUTING_ROUTES
    attempted_units = set((inspection or {}).get("execution_by_unit") or {})
    # One unit per distinct implementation, from the same record that scopes the obligations. Only the
    # IMPLEMENTATION units are this function's business: found by running the reassessment on study 65,
    # which reports three experiments and publishes nine scripts. Three experiment definitions made the
    # map non-empty, the fallback never fired, every definition was skipped for not being an
    # implementation, and a paper with nine published scripts reported no follow-up at all.
    units = analysis_units(plan=plan, evidence=evidence)
    definitions = {
        unit_id: definition
        for unit_id, definition in (units["definitions"] or {}).items()
        if definition.get("kind") == "implementation"
    } or {
        # No implementation units, which happens when the paper supplies one script (nothing to split)
        # or more than the ceiling (bioAF declined to split). Either way the follow-up is per script.
        f"code:{str(s.get('path') or 'an unnamed file')}": {
            "kind": "implementation",
            "paths": [str(s.get("path") or "an unnamed file")],
            "label": str(s.get("path") or "an unnamed file"),
            "language": str(s.get("language") or "").lower() or None,
        }
        for s in authors
    }
    followups: list[dict] = []
    for unit_id, definition in sorted(definitions.items()):
        own = [s for s in authors if str(s.get("path") or "an unnamed file") in set(definition.get("paths") or [])]
        if not own:
            continue
        language = str(own[0].get("language") or "").lower()
        runtime = _runtime_for(language)
        row = {
            "unit": unit_id,
            "paths": [str(s.get("path")) for s in own],
            "language": language or None,
            "runtime": runtime,
            "source": {
                "repo_url": resolution.get("url"),
                "commit_sha": resolution.get("commit_sha"),
                "digest": _digest(own),
            },
            "claimed_analysis": _claimed_analysis(plan),
            "inputs": {
                "processed_available": inputs_available,
                "answer": processed or "not established",
            },
            "attempted": unit_id in attempted_units,
        }
        if runtime is None:
            followups.append(
                {
                    **row,
                    "action": BLOCKED,
                    "missing": RUNTIME,
                    "reason": (
                        f"bioAF supplies no runtime for {language or 'this language'}, so it cannot build or load "
                        "this implementation at all"
                    ),
                    "next_action": f"add a supported runtime for {language or 'this language'}",
                }
            )
            continue
        if not authorized:
            followups.append(
                {
                    **row,
                    "action": NEEDS_AUTHORIZATION,
                    "missing": AUTHORIZATION,
                    "reason": (
                        "this validation was requested as an assessment, which spends no compute, so the authors' "
                        "code has not been run"
                    ),
                    "next_action": "choose to reproduce this paper's results, which authorises what running it costs",
                }
            )
            continue
        if inputs_available:
            followups.append(
                {
                    **row,
                    "action": ATTEMPT_REPRODUCTION,
                    "missing": None,
                    "reason": (
                        f"the paper published this implementation and bioAF can reach processed inputs for "
                        f"{row['claimed_analysis']}, so the attempt runs and its output is compared with the paper's"
                    ),
                    "next_action": "run the authors' analysis on the corresponding processed samples",
                }
            )
            continue
        followups.append(
            {
                **row,
                "action": ATTEMPT_BOUNDED,
                "missing": INPUT,
                "reason": (
                    "no pre-processed data bioAF can reach was found for this paper, so a full reproduction has "
                    "nothing to run on; the bounded check builds the declared environment, loads this "
                    "implementation and checks the interfaces it uses"
                ),
                "next_action": "run the bounded build and load, and record exactly what it establishes",
            }
        )
    return {
        "followups": followups,
        "reason": None
        if followups
        else "bioAF holds source for this paper and could attribute none of it to an implementation",
    }


def _digest(rows: list[dict]) -> str:
    import hashlib

    payload = "".join(f"{row.get('path')}\x00{row.get('text') or ''}\x00" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
