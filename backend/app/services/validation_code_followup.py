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

The pure follow-up description and the driver of its persisted execution operations share this module.
"""

from __future__ import annotations

from typing import cast

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
    authorized = str(route or "") in EXECUTING_ROUTES
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
        binding = ((inspection or {}).get("bindings") or {}).get(unit_id) or {}
        inputs_available = bool(
            binding.get("compatible") and binding.get("input_file_ids") and binding.get("source_digest") == _digest(own)
        )
        experiment = next(
            (e for e in plan.get("reported_experiments") or [] if e.get("id") == binding.get("experiment_id")), None
        )
        inputs_available = inputs_available and experiment is not None
        row = {
            "unit": unit_id,
            "paths": [str(s.get("path")) for s in own],
            "language": language or None,
            "runtime": runtime,
            "source": {
                "repo_url": resolution.get("url"),
                "commit_sha": resolution.get("commit_sha"),
                "digest": _digest(own),
                "dependencies_digest": _digest([*authors, *((inspection or {}).get("manifests") or [])]),
            },
            "claimed_analysis": _claimed_analysis({"reported_experiments": [experiment]})
            if experiment
            else "the supplied implementation; its relationship to the paper's analyses is not yet established",
            "inputs": {
                "processed_available": inputs_available,
                "answer": binding.get("reason") or "compatible inputs for this implementation are not established",
                "file_ids": binding.get("input_file_ids") if inputs_available else [],
            },
            "binding": binding if inputs_available else {},
            "attempted": bool((current_execution(inspection, unit_id, own) or {}).get("invocation", {}).get("invoked")),
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
                    "compatible processed inputs have not been bound to this implementation; attempt its "
                    "actual entry point within the bounded execution limits and record what it establishes"
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

    payload = "".join(f"{row.get('path')}\x00{row.get('original_text', row.get('text')) or ''}\x00" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_execution(inspection, unit_id, sources):
    """Credit an invocation only to the source and dependency revision it actually ran."""
    execution = (inspection.get("execution_by_unit") or {}).get(unit_id)
    if execution and execution.get("source_digest"):
        dependencies = [s for s in inspection.get("sources") or [] if not s.get("generated")]
        if execution["source_digest"] != _digest(sources) or execution.get("dependencies_digest") != _digest(
            [*dependencies, *(inspection.get("manifests") or [])]
        ):
            return None
    return execution


def invocation_outcome(transcript: str, *, compute_status: str) -> dict:
    """Only an observed author invocation establishes a run; imports and container success do not."""
    import re

    exits = re.findall(r"^BIOAF_AUTHOR_EXIT (\d+)\s*$", transcript, re.M)
    started = "BIOAF_SANDBOX_READY" in transcript
    code = int(exits[-1]) if exits else None
    if "BIOAF_SANDBOX_UNAVAILABLE" in transcript or "BIOAF_RUNTIME_UNAVAILABLE" in transcript:
        status, reason = "blocked", "This runner cannot supply the required isolated runtime."
    elif not started:
        status, reason = "inconclusive", "The run did not reach the author's entry point; inspect the build transcript."
    elif code == 0 and compute_status == "completed":
        status, reason = (
            "succeeded",
            "The author's entry point completed; comparison with the paper is a separate result.",
        )
    elif code in (124, 137) or (code is None):
        status, reason = "inconclusive", "The invocation ended without a complete result, or exceeded its limits."
    elif any(
        word in transcript for word in ("FileNotFoundError", "No such file", "cannot open file", "required arguments")
    ):
        status, reason = "blocked", "The author invocation needs an input or argument that was not available."
    else:
        status, reason = (
            "failed",
            "The author's entry point returned an error; the transcript records the observed cause.",
        )
    return {"status": status, "reason": reason, "invoked": started, "exit_code": code, "reproduced": False}


async def bind_execution_inputs(session, study, plan, *, client, model, api_key):
    """Bind held inputs and a stated command to each implementation using the configured assessor."""
    import json
    from sqlalchemy import select
    from app.models.file import File
    from app.services.llm_decision import decide_with_recovery
    from app.services.notebook_execution_service import _build_relative_path, _resolve_input_file_context
    from app.services.validation_check_queue import fingerprint
    from app.services.validation_decision_budgets import CODE_EXECUTION

    evidence = dict(study.evidence_json or {})
    ids = (evidence.get("level3") or {}).get("input_file_ids") or []
    if not ids or client is None:
        return
    files = (
        (await session.execute(select(File).where(File.id.in_(ids), File.organization_id == study.organization_id)))
        .scalars()
        .all()
    )
    context = await _resolve_input_file_context(session, {f.id: f for f in files})
    inputs = [{"id": f.id, "path": "/data/" + _build_relative_path(f, context), "uri": f.storage_uri} for f in files]
    inspection = dict(evidence.get("code_inspection") or {})
    bindings = dict(inspection.get("bindings") or {})
    for row in code_followup(evidence=evidence, plan=plan, route=study.intended_route)["followups"]:
        if row["action"] in (BLOCKED, NEEDS_AUTHORIZATION):
            continue
        own = [s for s in inspection.get("sources") or [] if s.get("path") in row["paths"]]
        identity = fingerprint({"sources": own, "inputs": inputs, "plan": plan, "model": model, "version": 1})
        previous = bindings.get(row["unit"], {})
        if previous.get("identity") == identity and not previous.get("failure_outcome"):
            continue
        experiments = {e["id"]: e for e in plan.get("reported_experiments") or [] if e.get("id")}

        def validate(data):
            if data.get("compatible") is not True:
                return []
            return (
                []
                if (
                    data.get("experiment_id") in experiments
                    and isinstance(data.get("arguments"), list)
                    and all(isinstance(a, str) for a in data["arguments"])
                    and bool(data.get("input_file_ids"))
                    and set(data["input_file_ids"]) <= {f.id for f in files}
                    and str(data.get("reason") or "").strip()
                )
                else ["name a supplied experiment, inputs, argument list and evidence"]
            )

        decision = await decide_with_recovery(
            intent="binding the author's implementation to its analysis inputs",
            purpose=CODE_EXECUTION,
            system=(
                "Return one fenced JSON object with compatible (boolean), experiment_id, input_file_ids, "
                "arguments (a list of command line arguments), and reason citing the code and input paths. "
                "Bind only the analysis this unchanged source actually performs to compatible held inputs. "
                "Do not invent missing files, alter the source, or treat an argument that just prints help as an analysis. "
                "When compatibility or a usable invocation cannot be established, compatible must be false."
            ),
            payload=json.dumps(
                {
                    "sources": own,
                    "manifests": inspection.get("manifests"),
                    "experiments": list(experiments.values()),
                    "inputs": inputs,
                }
            ),
            client=client,
            model=model,
            api_key=api_key,
            validate=validate,
        )
        bindings[row["unit"]] = {
            **(decision.data if decision.ok else {}),
            "identity": identity,
            "source_digest": row["source"]["digest"],
            "input_identity": fingerprint(inputs),
            "model": model,
            "failure_outcome": None if decision.ok else decision.outcome,
            "reason": (decision.data or {}).get("reason") if decision.ok else decision.reason,
        }
        bound = bindings[row["unit"]]
        experiment = experiments.get(bound.get("experiment_id"), {})
        indices = set(experiment.get("claim_indices") or [])
        bound["claims"] = [c for i, c in enumerate(evidence.get("comparison_targets") or []) if i in indices]
        selected = (plan.get("analysis_selection") or {}).get("current") or {}
        if selected.get("reported_experiment_id") == bound.get("experiment_id") and bound.get("experiment_id"):
            bound["comparison_contract"] = evidence.get("level3") or {}
    inspection["bindings"] = bindings
    study.evidence_json = {**(study.evidence_json or {}), "code_inspection": inspection}
    await session.flush()


def execution_bundle(inspection, row):
    """Preserve file paths and original documents; never fabricate an author's missing source."""
    from app.adapters.notebooks.kubernetes import _safe_entry_point
    from app.services.validation_environment_check import _archive, EnvironmentCheckRefused

    members, paths = {}, {}
    for source in [*(inspection.get("sources") or []), *(inspection.get("manifests") or [])]:
        if source.get("generated"):
            continue
        path = str(source.get("path") or "")
        container = (source.get("provenance") or {}).get("container")
        original = source.get("original_text")
        actual = container if original is not None and container else path
        if " > " in actual or _safe_entry_point(actual) != actual:
            if path in row["paths"]:
                raise EnvironmentCheckRefused(
                    "The supplied document has no standalone executable file; a stated invocation is needed."
                )
            continue
        if actual.lower().endswith((".ipynb", ".rmd")) and original is None:
            raise EnvironmentCheckRefused(
                "Retrieve the original document before attempting it; held excerpts are insufficient."
            )
        text = original if original is not None else source.get("text") or ""
        if "bundle/" + actual in members and members["bundle/" + actual] != text:
            raise EnvironmentCheckRefused("The held sources contain conflicting versions of the same executable path.")
        members["bundle/" + actual] = text
        paths[path] = actual
    entry = paths.get(row["paths"][0])
    if not entry:
        raise EnvironmentCheckRefused("The author's entry point is not present in the held source bundle.")
    return _archive(members), entry


async def advance_code_followups(session, study, *, plan=None, claim=None):
    """Advance one author operation under the existing study driver and ownership claim."""
    import shlex
    from app.adapters.registry import get_storage_adapter
    from app.services.notebook_execution_service import NotebookExecutionService
    from app.services.validation_check_queue import fingerprint
    from app.services.validation_environment_check import (
        EnvironmentCheckRefused,
        RESOURCE_PROFILE,
        TIMEOUT_SECONDS,
        _compute_session,
    )
    from app.services.untrusted_execution import untrusted_identity, UNCONFIGURED_MESSAGE
    from app.services.validation_ownership import (
        begin_operation,
        record_dispatch,
        finish_operation,
        assert_held,
        ClaimLost,
    )

    evidence = dict(study.evidence_json or {})
    held = dict(evidence.get("code_followup") or {})
    rows = [dict(r) for r in held.get("followups") or []]
    inspection = dict(evidence.get("code_inspection") or {})
    for row in rows:
        if row.get("action") not in (ATTEMPT_REPRODUCTION, ATTEMPT_BOUNDED):
            continue
        key = "author_code:" + fingerprint(
            {"unit": row["unit"], "source": row["source"], "binding": row.get("binding"), "version": 1}
        )
        if row.get("status") in ("settled", "blocked"):
            continue
        try:
            if not row.get("session_id"):
                identity = await untrusted_identity(session)
                if identity is None:
                    raise EnvironmentCheckRefused(UNCONFIGURED_MESSAGE)
                blob, entry = execution_bundle(inspection, row)
                storage = get_storage_adapter()
                uri = storage.build_uri(identity.bucket, f"{identity.prefix_for(study.id)}/{key}/source.tar.gz")
                await storage.write_bytes(uri, blob, content_type="application/gzip")
                operation = await begin_operation(session, study, key, kind="author_code", claim=claim)
                await session.commit()
                contract = {
                    "language": row["language"],
                    "network": "install_only",
                    "working_directory": "/work",
                    "filesystem": "read-only inputs; writable /work, /tmp and /outputs",
                }
                await assert_held(session, claim)
                compute = await NotebookExecutionService.execute_fetched_code(
                    session,
                    org_id=study.organization_id,
                    user_id=study.requested_by_user_id,
                    code_uri=uri,
                    entry_point=entry,
                    arguments=shlex.join((row.get("binding") or {}).get("arguments") or []),
                    input_file_ids=row["inputs"].get("file_ids") or [],
                    experiment_id=study.experiment_id,
                    resource_profile=RESOURCE_PROFILE,
                    timeout_seconds=TIMEOUT_SECONDS,
                    execution_contract=contract,
                    operation_key=operation["operation_id"],
                )
                await record_dispatch(session, study, key, external_id=compute.id, claim=claim)
                row.update(
                    status="running",
                    session_id=compute.id,
                    operation_id=operation["operation_id"],
                    operation_key=key,
                    entry_point=entry,
                    effective=(compute.provider_metadata or {}).get("execution_contract"),
                )
            else:
                from app.services.validation_driver_service import ValidationDriverService

                compute = await _compute_session(session, row["session_id"])
                if compute is None:
                    raise EnvironmentCheckRefused("The recorded execution session no longer exists.")
                compute = await NotebookExecutionService.poll_execution(session, compute)
                if compute.status == "stopped":
                    raise EnvironmentCheckRefused("The author-code session was stopped before completion.")
                if compute.status not in ("completed", "failed"):
                    break
                outputs = await ValidationDriverService._read_code_outputs(session, compute)
                transcript = "\n".join(
                    o.get("text") or "" for o in outputs if str(o.get("path")).endswith("transcript.txt")
                )
                outcome = invocation_outcome(transcript, compute_status=compute.status)
                if not outcome["invoked"] and getattr(compute, "failure_message", None):
                    outcome["reason"] += " " + cast(str, compute.failure_message)
                import re

                runtime = re.search(r"BIOAF_RUNTIME\n([^\n]+)", transcript)
                effective = dict(row.get("effective") or {})
                effective.update(
                    runtime_verified=outcome["invoked"],
                    applied=outcome["invoked"],
                    runtime_version=runtime.group(1) if runtime else None,
                )
                result_files = [o for o in outputs if not str(o.get("path")).endswith("transcript.txt")]
                comparisons = (
                    compare_author_outputs(result_files, row.get("binding") or {}, evidence, plan or {})
                    if (row["action"] == ATTEMPT_REPRODUCTION and outcome["status"] == "succeeded")
                    else []
                )
                outcome["comparisons"] = comparisons
                outcome["reproduced"] = bool(comparisons) and all(
                    c.get("within_tolerance") is True for c in comparisons
                )
                row.update(
                    status="settled",
                    attempted=outcome["invoked"],
                    outcome=outcome,
                    reason=outcome["reason"],
                    effective=effective,
                    transcript=transcript[-20000:],
                    outputs=[o.get("path") for o in result_files],
                )
                inspection["execution_by_unit"] = {
                    **(inspection.get("execution_by_unit") or {}),
                    row["unit"]: {
                        "invocation": outcome,
                        "operation_id": row.get("operation_id"),
                        "source_digest": row["source"]["digest"],
                        "dependencies_digest": row["source"]["dependencies_digest"],
                    },
                }
                await finish_operation(session, study, key)
        except ClaimLost:
            raise
        except Exception as exc:
            row.update(
                status="blocked",
                reason=str(exc),
                next_action="resolve this execution prerequisite and retry the assessment",
            )
        break  # One active author job per study; the next tick polls it or starts the next implementation.
    held.update(
        followups=rows,
        pending=any(
            r.get("action") in (ATTEMPT_BOUNDED, ATTEMPT_REPRODUCTION) and r.get("status") not in ("settled", "blocked")
            for r in rows
        ),
        blocked=[
            {"action": r["action"], "reason": r["reason"]}
            for r in rows
            if r.get("status") == "blocked" or r.get("action") == BLOCKED
        ],
        scheduled=[r["operation_id"] for r in rows if r.get("session_id")],
    )
    study.evidence_json = {**(study.evidence_json or {}), "code_followup": held, "code_inspection": inspection}
    await session.flush()
    return held


def compare_author_outputs(outputs, binding, evidence, plan):
    """Use the existing numeric claim comparator, only for the experiment bound before execution."""
    from app.services.code_execution_service import adapt_outputs
    from app.services.validation_classifier_service import compare_targets

    claims = binding.get("claims") or []
    adapted = adapt_outputs(outputs=outputs, claims=claims)
    if adapted.get("kind") == "differential_table":
        from app.services.result_set_normalizer import FindingSet, normalize_gene_table, normalize_interval_table
        from app.services.validation_concordance_service import compare_gene_sets, compare_interval_sets
        from app.services.validation_claim_cutoffs import normalizer_arguments

        target = binding.get("comparison_contract") or {}
        applied = normalizer_arguments(target.get("cutoffs"), legacy=target.get("parameters") or {})
        if not target.get("paper_finding_set") or applied is None:
            return []
        paper = FindingSet.from_dict(target["paper_finding_set"])
        ours = (normalize_interval_table if target.get("kind") == "interval" else normalize_gene_table)(
            adapted["table_text"], **applied
        )
        compare = compare_interval_sets if target.get("kind") == "interval" else compare_gene_sets
        result = compare(
            paper, ours, int(target.get("universe") or ours.n_tested or max(len(paper.entities), len(ours.entities), 1))
        )
        return [
            {
                "metric_key": "author differential finding set",
                "claimed_value": len(paper.entities),
                "computed_value": len(ours.entities),
                "within_tolerance": True
                if result.verdict == "agree"
                else False
                if result.verdict == "diverge"
                else None,
                "concordance": result.to_dict(),
            }
        ]
    if adapted.get("kind") != "numeric_claim":
        return []
    comparisons = compare_targets(claims, adapted.get("computed_metrics"))
    for comparison in comparisons:
        if comparison.get("advisory"):
            comparison.update(within_tolerance=None, verdict="not_compared")
    return comparisons
