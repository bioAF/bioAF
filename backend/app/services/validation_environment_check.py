"""plan_8_5 section 3.5: the route for the two code obligations that can only be settled by running.

C1.B (the supplied source loads in its declared environment) and C2.B (its dependency versions and
interfaces resolve together) cannot be established by reading. Loading a module executes it, and
resolving dependencies installs them, so rubric v3 verifies both only from a RECORDED result of the
isolated execution path, under the approval that path requires.

What was missing was the route. Nothing could produce such a record, so both obligations were grey
on every study with no way forward, which reads as a permanent capability limit and is really an
unbuilt path. This module is that route's near half:

- the **request** a person approves: what would run, in which runtime, with which files, under which
  limits, and which obligations the result would settle;
- the **recording**: where a result lands so the obligation reads it, without disturbing the reviews
  and the source already held beside it.

Nothing here executes anything, and building a request never evaluates the source it describes.
Submitting the request is the isolated path's business, and a study with no approved run keeps both
obligations untested.
"""

from __future__ import annotations

# The runtimes bioAF can offer for a bounded environment check, and what each one is asked to do.
RUNTIMES = {
    "python": {"runtime": "python:3.12-slim", "action": "import every module the source imports, and nothing else"},
    "r": {"runtime": "R 4.4 (rocker/r-ver)", "action": "attach every package the source attaches, and nothing else"},
}

# Bounded, and never negotiable by the code being checked.
LIMITS = {
    "cpu": "1",
    "memory": "2Gi",
    "timeout_seconds": 300,
    "network": "denied",
    "filesystem": "the study's own prefix, deleted after the run",
}

ESTABLISHES = ("C1.B", "C2.B")


class EnvironmentCheckRefused(ValueError):
    """The check cannot be requested, and the words say why."""


def environment_check_request(*, sources: list[dict] | None, manifests: list[dict] | None) -> dict:
    """What an isolated environment check for this analysis would be, for a person to approve.

    One language per request: a run that loads Python and R together establishes neither one's
    environment, and the two obligations are about the environment the paper declared.
    """
    supplied = [s for s in sources or [] if isinstance(s, dict) and not s.get("generated")]
    if not supplied:
        raise EnvironmentCheckRefused(
            "bioAF holds no source for this paper's analysis, so there is nothing to load"
        )
    languages = sorted({str(s.get("language") or "").strip().lower() for s in supplied} - {""})
    unsupported = [language for language in languages if language not in RUNTIMES]
    if unsupported:
        raise EnvironmentCheckRefused(
            f"bioAF supplies no runtime for {', '.join(unsupported)}, so it cannot load this source in the "
            "environment the paper declared"
        )
    language = languages[0]
    files = [str(s.get("path")) for s in supplied if str(s.get("language") or "").lower() == language]
    files += [str(m.get("path")) for m in manifests or [] if isinstance(m, dict) and m.get("path")]
    return {
        "language": language,
        "runtime": RUNTIMES[language]["runtime"],
        "action": RUNTIMES[language]["action"],
        "files": files,
        "limits": dict(LIMITS),
        "establishes": list(ESTABLISHES),
        "approval": {
            "required": True,
            "reason": (
                "loading the paper's source and resolving its dependencies runs code bioAF did not write, "
                "so it runs in the isolated execution path under an explicit approval"
            ),
        },
    }


def record_environment_check(evidence: dict, *, result: dict) -> dict:
    """Land an isolated check's result where C1.B and C2.B read it, keeping everything else held.

    ``result`` carries ``load`` and ``dependency_resolution``, each ``{"status", "ref", ...}``. An
    earlier review, an earlier run and the source itself are evidence in their own right and are not
    discarded because another run was recorded.
    """
    inspection = dict(evidence.get("code_inspection") or {})
    execution = dict(inspection.get("execution") or {})
    for key in ("load", "dependency_resolution"):
        if isinstance((result or {}).get(key), dict):
            execution[key] = dict(result[key])
    inspection["execution"] = execution
    evidence["code_inspection"] = inspection
    return execution
