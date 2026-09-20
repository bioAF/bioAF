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

import logging
from datetime import datetime, timezone

logger = logging.getLogger("bioaf.validation_environment_check")

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


# What the check prints, so a transcript can be read back into an outcome. A marker rather than a
# parse of free text: the pod's own stderr and a package's warnings share that stream.
LOAD_MARKER = "BIOAF_LOAD"
RESOLVE_MARKER = "BIOAF_RESOLVE"


def _python_check(modules: list[str]) -> str:
    """Import each module on its own, so a failure names ONE of them."""
    return (
        "import importlib, sys\n"
        f"modules = {sorted(modules)!r}\n"
        "failed = []\n"
        "for name in modules:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        f"        print('{LOAD_MARKER} ' + name + ' ok', flush=True)\n"
        "    except BaseException as exc:\n"
        "        failed.append(name)\n"
        f"        print('{LOAD_MARKER} ' + name + ' failed: ' + str(exc), flush=True)\n"
        f"print('{RESOLVE_MARKER} ' + ('failed: ' + ', '.join(failed) if failed else 'ok'), flush=True)\n"
        "sys.exit(1 if failed else 0)\n"
    )


def _r_check(packages: list[str]) -> str:
    """Attach each package on its own. ``requireNamespace`` loads without attaching, which is the
    narrower thing to ask and still answers whether the environment holds it."""
    return (
        f"packages <- c({', '.join(repr(p) for p in sorted(packages))})\n"
        "failed <- character(0)\n"
        "for (name in packages) {\n"
        "  ok <- requireNamespace(name, quietly = TRUE)\n"
        "  if (ok) {\n"
        f"    cat('{LOAD_MARKER} ', name, ' ok\\n', sep = '')\n"
        "  } else {\n"
        "    failed <- c(failed, name)\n"
        f"    cat('{LOAD_MARKER} ', name, ' failed: not installed\\n', sep = '')\n"
        "  }\n"
        "}\n"
        f"cat('{RESOLVE_MARKER} ', if (length(failed)) paste0('failed: ', paste(failed, collapse = ', ')) "
        "else 'ok', '\\n', sep = '')\n"
        "quit(status = if (length(failed)) 1 else 0)\n"
    )


def _required(sources: list[dict], language: str) -> list[str]:
    """What this analysis requires, read from its own source and nothing else."""
    if language == "python":
        import ast

        modules: set[str] = set()
        for source in sources:
            try:
                tree = ast.parse(str(source.get("text") or ""))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    modules.add(node.module.split(".")[0])
        return sorted(modules)
    from app.services.r_parser import read_r

    packages: set[str] = set()
    for source in sources:
        found = read_r(str(source.get("text") or ""))
        packages |= set(found["packages"]) | set(found["namespaced"])
    return sorted(packages)


def check_script(*, sources: list[dict] | None, manifests: list[dict] | None) -> str:
    """The script the isolated run executes: load what the source requires, and nothing else.

    It never runs the paper's own script. Loading a module executes that module's top level, which is
    what makes this an isolated run at all, but running the analysis is a different question with a
    different cost and a different approval.
    """
    request = environment_check_request(sources=sources, manifests=manifests)
    supplied = [s for s in sources or [] if str(s.get("language") or "").lower() == request["language"]]
    required = _required(supplied, request["language"])
    return _python_check(required) if request["language"] == "python" else _r_check(required)


_ENTRY_POINT = {"python": "bioaf_environment_check.py", "r": "bioaf_environment_check.R"}


def _archive(members: dict[str, str]) -> bytes:
    """One gzipped tar of what the check needs, which is what the runner knows how to fetch.

    Caught by running it live: the fetched-code runner copies ``code_uri`` to a single local file
    and unpacks it. Staging a directory prefix made it fetch nothing, run nothing, and exit clean.
    """
    import io
    import tarfile
    import time

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in members.items():
            payload = (text or "").encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            info.mtime = int(time.time())
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _member_name(path: str, taken: set[str]) -> str:
    """A flat, safe name inside the archive. A source extracted from a document carries its
    document's title, which is not a filename anyone can run."""
    import re

    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(path or "").rsplit("/", 1)[-1]).strip("_") or "source"
    name, suffix = base, 1
    while name in taken:
        suffix += 1
        stem, _, extension = base.rpartition(".")
        name = f"{stem}_{suffix}.{extension}" if stem else f"{base}_{suffix}"
    taken.add(name)
    return name


async def stage_environment_check(*, sources, manifests, storage, bucket: str, prefix: str) -> dict:
    """Put the sources, their manifests and the check where the isolated run can read them.

    ONE object: the runner copies ``code_uri`` to a single local file and unpacks it, so a tarball is
    what it can read. Only what it was given goes in, and one check script beside it.
    """
    request = environment_check_request(sources=sources, manifests=manifests)
    entry_point = _ENTRY_POINT[request["language"]]
    taken: set[str] = {entry_point}
    members: dict[str, str] = {}
    for row in [*(sources or []), *(manifests or [])]:
        if not str(row.get("path") or "").strip():
            continue
        members[_member_name(str(row.get("path")), taken)] = str(row.get("text") or "")
    members[entry_point] = check_script(sources=sources, manifests=manifests)
    uri = storage.build_uri(bucket, f"{prefix}/environment-check.tar.gz")
    await storage.write_bytes(uri, _archive(members), content_type="application/gzip")
    return {
        "code_uri": uri,
        "entry_point": entry_point,
        "language": request["language"],
        "files": sorted(members),
        "request": request,
    }


def outcome_from_run(*, exit_code: int | None, transcript: str, environment: str, ref: str) -> dict:
    """What an isolated run established, read from its own markers.

    plan_8_4 section 3.4: a run that said nothing establishes NOTHING. A pod killed for memory, a
    timeout, an image that could not start are all bioAF's limitations, and reading them as a paper
    whose code will not load is exactly the confusion this rubric exists to prevent.
    """
    lines = [line.strip() for line in (transcript or "").splitlines() if line.strip()]
    loads = [line for line in lines if line.startswith(LOAD_MARKER)]
    resolves = [line for line in lines if line.startswith(RESOLVE_MARKER)]
    if not loads and not resolves:
        return {}
    found: dict = {}
    broken = [line[len(LOAD_MARKER) :].strip() for line in loads if " failed:" in line]
    # plan_8_4 section 3.4: a failure of bioAF's own environment is undetermined, never failed. The
    # run uses whatever image this install configures, and an image that simply does not hold the
    # paper's ecosystem makes every package "missing". A paper whose every single declared package
    # is broken is far more likely an environment nobody provisioned, so it establishes nothing.
    if len(broken) > 1 and len(broken) == len(loads):
        return {}
    if broken:
        found["load"] = {
            "status": "failed",
            "reason": "; ".join(broken),
            "environment": environment,
            "ref": ref,
        }
    elif loads and exit_code == 0:
        found["load"] = {"status": "succeeded", "environment": environment, "ref": ref}
    for line in resolves:
        answer = line[len(RESOLVE_MARKER) :].strip()
        if answer.startswith("failed"):
            found["dependency_resolution"] = {
                "status": "failed",
                "reason": answer.partition(":")[2].strip() or answer,
                "environment": environment,
                "ref": ref,
            }
        elif answer == "ok" and exit_code == 0:
            found["dependency_resolution"] = {"status": "succeeded", "environment": environment, "ref": ref}
    return found


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


# ---- the run a person approves ---------------------------------------------------------------------
#
# plan_8_5 section 3.5. The isolation was never the missing part: `execute_fetched_code` already runs
# code fetched from a paper's authors under plan_7 step 16a's identity, in its own namespace, holding
# no project-level role. What had no caller was the check that settles C1.B and C2.B.


def get_storage_adapter():
    """Indirected so a test can stage without a bucket, and so this module names its own dependency."""
    from app.adapters.registry import get_storage_adapter as adapter

    return adapter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sources_of(study) -> tuple[list[dict], list[dict]]:
    inspection = (study.evidence_json or {}).get("code_inspection") or {}
    sources = [s for s in inspection.get("sources") or [] if isinstance(s, dict)]
    manifests = [m for m in inspection.get("manifests") or [] if isinstance(m, dict)]
    return sources, manifests


async def request_environment_check(session, study, *, user_id: int) -> dict:
    """Stage this study's source and submit the environment check under the isolated identity.

    Raises ``EnvironmentCheckRefused`` where there is nothing to check, no runtime for it, or no
    isolated identity on this install. The last one is deliberate: borrowing another identity is the
    exposure that identity exists to end.
    """
    from app.services.notebook_execution_service import NotebookExecutionService
    from app.services.untrusted_execution import UNCONFIGURED_MESSAGE, untrusted_identity

    # Found by using it: asking again while a pod was still running launched a second one. The
    # control a person has is the same one that completes the loop, so a pending check is polled.
    held = ((study.evidence_json or {}).get("code_inspection") or {}).get("environment_check") or {}
    if held.get("status") == "running":
        return await settle_environment_check(session, study)

    sources, manifests = _sources_of(study)
    request = environment_check_request(sources=sources, manifests=manifests)
    identity = await untrusted_identity(session)
    if identity is None:
        raise EnvironmentCheckRefused(UNCONFIGURED_MESSAGE)
    staged = await stage_environment_check(
        sources=sources,
        manifests=manifests,
        storage=get_storage_adapter(),
        bucket=identity.bucket,
        prefix=f"{identity.prefix_for(study.id)}/environment-check",
    )
    compute = await NotebookExecutionService.execute_fetched_code(
        session,
        org_id=study.organization_id,
        user_id=user_id,
        code_uri=staged["code_uri"],
        entry_point=staged["entry_point"],
        arguments="",
        input_file_ids=[],
        experiment_id=getattr(study, "experiment_id", None),
    )
    record = {
        "status": "running",
        "session_id": compute.id,
        "language": staged["language"],
        "runtime": request["runtime"],
        "files": staged["files"],
        "establishes": request["establishes"],
        "limits": request["limits"],
        "requested_by": user_id,
        "at": _now_iso(),
    }
    evidence = dict(study.evidence_json or {})
    inspection = dict(evidence.get("code_inspection") or {})
    inspection["environment_check"] = record
    evidence["code_inspection"] = inspection
    study.evidence_json = evidence
    await session.flush()
    return record


# The pod writes its log to `/outputs/transcript.txt` and it is registered as an output file, so the
# transcript arrives the way every other fetched-code run's does. Caught by running this live: there
# is no `output_log` on a compute session, and reading one would have made every check inconclusive.
TRANSCRIPT_FILENAME = "transcript.txt"


async def _compute_session(session, session_id: int):
    from app.models.notebook_session import ComputeSession

    return await session.get(ComputeSession, session_id)


async def _transcript_of(session, compute) -> str:
    """What the isolated run said: its failure message and the log it wrote, the way the code arm
    reads them. Never raises: an unreadable transcript is a check that established nothing."""
    from app.services.validation_driver_service import ValidationDriverService

    transcript = str(getattr(compute, "failure_message", "") or "")
    try:
        outputs = await ValidationDriverService._read_code_outputs(session, compute)
    except Exception as exc:  # noqa: BLE001 - an unreadable output is not a paper's defect
        logger.warning("the environment check's transcript could not be read: %s", exc)
        return transcript
    for out in outputs or []:
        if str(out.get("path", "")).rsplit("/", 1)[-1] == TRANSCRIPT_FILENAME:
            transcript = f"{transcript}\n{out.get('text') or ''}".strip()
    return transcript


async def settle_environment_check(session, study) -> dict:
    """Read a submitted check's result and land what it established, or nothing.

    plan_8_4 section 3.4: a run that said nothing establishes NOTHING. A pod killed for memory, a
    timeout and an image that could not start are bioAF's limitations, and reading any of them as a
    paper whose code will not load is the confusion this rubric exists to prevent.
    """
    from app.services.notebook_execution_service import NotebookExecutionService

    evidence = dict(study.evidence_json or {})
    inspection = dict(evidence.get("code_inspection") or {})
    record = dict(inspection.get("environment_check") or {})
    if not record or record.get("status") not in ("running", None):
        return record
    compute = await _compute_session(session, record.get("session_id"))
    if compute is None:
        return record
    try:
        compute = await NotebookExecutionService.poll_execution(session, compute)
    except Exception as exc:  # noqa: BLE001 - polling a run cannot fail the study it belongs to
        logger.warning("study %s: the environment check could not be polled: %s", study.id, exc)
        return record
    if getattr(compute, "status", None) in ("running", "pending", "starting"):
        return record
    transcript = await _transcript_of(session, compute)
    found = outcome_from_run(
        # A compute session carries no exit code of its own: a pod that ended in `failed` is the 1.
        exit_code=1 if getattr(compute, "status", None) == "failed" else 0,
        transcript=transcript,
        environment=record.get("runtime") or "the declared environment",
        ref=f"compute-session-{record.get('session_id')}",
    )
    record["status"] = "settled" if found else "inconclusive"
    record["settled_at"] = _now_iso()
    if not found:
        record["reason"] = (
            "the isolated run ended without reporting on a single module, so it established nothing "
            "about this paper's code"
        )
    inspection["environment_check"] = record
    evidence["code_inspection"] = inspection
    study.evidence_json = evidence
    if found:
        record_environment_check(evidence, result=found)
        study.evidence_json = dict(evidence)
    await session.flush()
    return record
