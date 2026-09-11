"""change_7.3 section 5: whether reproduction was attempted, derived from what actually executed.

The headline followed the classification bucket, and `inconclusive` rendered "Could Not Reproduce"
whether anything ran or not. Study 34 executed nothing: no pipeline run, no code, no notebook. It
still read as a reproduction that had been tried and had failed, which is a statement about the paper
the evidence does not support.

**Only execution counts.** An attempt is an analysis that executed on acquired inputs: an analysis
pipeline run, the authors' code, a generated analysis, or a reproduction notebook. Resolving,
retrieving or inspecting resources is not an attempt, and neither is a consistency check against a
published results table, however much evidence either one produces.

**Acquiring inputs without running anything on them is not an attempt either** (the plan's open
question 2, answered as the plan recommends). It is an acquisition, and its limitation is reported as
one.

This is a projection of recorded facts. It decides nothing that is not already on the study.
"""

from __future__ import annotations

from app.services.code_execution_service import CODE_ABSENT, CODE_UNREACHABLE, GENERATION_FAILED

ATTEMPTED = "attempted"
NOT_ATTEMPTED = "not_attempted"

# Code outcomes that mean nothing was launched: there was no code, it could not be fetched, or no
# runnable analysis could be generated from the methods. Every other outcome follows a launch.
_NEVER_LAUNCHED = (CODE_ABSENT, CODE_UNREACHABLE, GENERATION_FAILED)


def reproduction_attempt(evidence: dict | None, *, analysis_run_id: int | None, data_run_id: int | None = None) -> dict:
    """``{"status", "executed", "acquired"}`` for one study.

    ``executed`` names what ran, in the reader's words; ``acquired`` names inputs bioAF went and got,
    which on their own never make an attempt.
    """
    evidence = evidence or {}
    executed: list[str] = []
    if analysis_run_id:
        executed.append("analysis pipeline run")
    if evidence.get("level3_run_session_id") or evidence.get("level3_result"):
        executed.append("reproduction notebook")
    code = evidence.get("code_execution") or {}
    if isinstance(code, dict) and code and code.get("outcome") not in _NEVER_LAUNCHED:
        executed.append("generated analysis" if code.get("method") == "llm_from_methods" else "authors' code")

    acquired: list[str] = []
    if evidence.get("deposit"):
        acquired.append("deposited files")
    if data_run_id:
        acquired.append("raw sequencing reads")

    return {
        "status": ATTEMPTED if executed else NOT_ATTEMPTED,
        "executed": list(dict.fromkeys(executed)),
        "acquired": acquired,
    }
