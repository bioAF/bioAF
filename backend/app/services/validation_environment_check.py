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
import re
from datetime import datetime, timezone

logger = logging.getLogger("bioaf.validation_environment_check")

# The runtimes bioAF can offer for a bounded environment check, and what each one is asked to do.
RUNTIMES = {
    "python": {"runtime": "python:3.12-slim", "action": "import every module the source imports, and nothing else"},
    "r": {"runtime": "R 4.4 (rocker/r-ver)", "action": "attach every package the source attaches, and nothing else"},
}

# Bounded, and never negotiable by the code being checked. plan_8_7 stage 3: these have to REACH the
# executor. `LIMITS` was recorded on the request and never passed to `execute_fetched_code`, so the
# report stated a cpu, a memory ceiling, a timeout and a network policy that nothing applied.
#
# `resource_profile` is the executor's own vocabulary, which is what makes the cpu and memory
# enforceable: `small` is (2, 8) in `notebook_service.RESOURCE_PROFILES`, and the request records the
# effective numbers beside the requested ones so an unenforced setting cannot read as an applied one.
RESOURCE_PROFILE = "small"
TIMEOUT_SECONDS = 900
FILESYSTEM = "the study's own prefix under the isolated identity's bucket"

# What the two phases each need of the network, declared separately because they differ. Installing
# the paper's declared dependencies needs a package index; loading them does not, and a check that
# quietly allowed egress for both would be recording a policy it was not applying.
NETWORK_DENIED = "denied"
NETWORK_ALLOWED = "allowed"
NETWORK_INSTALL_ONLY = "install_only"

LIMITS = {
    "cpu": "2",
    "memory": "8Gi",
    "timeout_seconds": TIMEOUT_SECONDS,
    "network": NETWORK_INSTALL_ONLY,
    "filesystem": FILESYSTEM,
}

ESTABLISHES = ("C1.B", "C2.B")


class EnvironmentCheckRefused(ValueError):
    """The check cannot be requested, and the words say why."""


def _digest(rows: list[dict]) -> str | None:
    """What was staged, as one hash, so a recorded result names the source revision it ran against."""
    import hashlib

    if not rows:
        return None
    payload = "".join(f"{row.get('path')}\x00{row.get('text') or ''}\x00" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


WORKING_DIRECTORY = "/workspace/fetched-code"


def environment_check_request(*, sources: list[dict] | None, manifests: list[dict] | None) -> dict:
    """The specification an approval authorizes, and the one the executor is given.

    plan_8_7 stage 3: "One approved execution specification must supply the actual runtime/image,
    source/input hashes, working directory, resource profile, timeout and network policy to the
    executor. Record both the requested and effective settings; refuse an unsupported execution
    specification rather than recording unenforced settings as if applied."

    One language per request: a run that loads Python and R together establishes neither one's
    environment, and the two obligations are about the environment the paper declared.
    """
    supplied = [s for s in sources or [] if isinstance(s, dict) and not s.get("generated")]
    if not supplied:
        raise EnvironmentCheckRefused("bioAF holds no source for this paper's analysis, so there is nothing to load")
    languages = sorted({str(s.get("language") or "").strip().lower() for s in supplied} - {""})
    unsupported = [language for language in languages if language not in RUNTIMES]
    if unsupported:
        raise EnvironmentCheckRefused(
            f"bioAF supplies no runtime for {', '.join(unsupported)}, so it cannot load this source in the "
            "environment the paper declared"
        )
    language = languages[0]
    own = [s for s in supplied if str(s.get("language") or "").lower() == language]
    held = [m for m in manifests or [] if isinstance(m, dict) and m.get("path")]
    files = [str(s.get("path")) for s in own] + [str(m.get("path")) for m in held]
    installs = bool(held)
    limits = {**LIMITS, "network": NETWORK_INSTALL_ONLY if installs else NETWORK_DENIED}
    return {
        "language": language,
        "runtime": RUNTIMES[language]["runtime"],
        "action": RUNTIMES[language]["action"],
        "files": files,
        # What was staged, so a recorded result names the revision it ran against rather than "the code".
        "source_digest": _digest(own),
        "manifest_digest": _digest(held),
        "working_directory": WORKING_DIRECTORY,
        "resource_profile": RESOURCE_PROFILE,
        "limits": limits,
        # The two phases, each with the network it actually needs. Installing the paper's declared
        # dependencies needs a package index; loading them does not.
        "phases": {
            "install": {
                "performed": installs,
                "network": NETWORK_ALLOWED if installs else NETWORK_DENIED,
                "reason": (
                    "the paper's declared dependencies are installed from its own manifests, which needs a "
                    "package index"
                )
                if installs
                else "bioAF holds no dependency specification for this analysis, so nothing is installed",
            },
            "runtime": {
                "performed": True,
                "network": NETWORK_DENIED,
                "reason": "loading, version and interface checks read the installed environment and nothing else",
            },
        },
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
#
# plan_8_7 stage 3: "Record installation, load/build, interface and analysis execution as distinct
# observations." Four markers because they are four different facts, and reading them as one is how a
# successful import against the base image came to stand for an environment nobody built.
INSTALL_MARKER = "BIOAF_INSTALL"
LOAD_MARKER = "BIOAF_LOAD"
VERSION_MARKER = "BIOAF_VERSION"
INTERFACE_MARKER = "BIOAF_INTERFACE"
RESOLVE_MARKER = "BIOAF_RESOLVE"


def _install_step(manifests: list[dict], language: str) -> str:
    """The shell that installs the paper's OWN declared dependencies, before anything is loaded.

    plan_8_7 stage 3: "Separate dependency acquisition from the runtime check where their network
    requirements differ." It is a separate step with its own marker for the same reason it is a
    separate network policy: a repository whose environment cannot be built is a fact about that
    paper, and it was invisible while the check only imported against whatever image this install
    configures.
    """
    paths = [str(m.get("path") or "").rsplit("/", 1)[-1] for m in manifests]
    if not paths:
        return f"echo '{INSTALL_MARKER} none'"
    steps = []
    for path in paths:
        lowered = path.lower()
        if lowered == "requirements.txt":
            steps.append(f"pip install --no-input -r '{path}'")
        elif lowered in ("environment.yml", "environment.yaml"):
            steps.append(f"conda env update -q -f '{path}'")
        elif lowered == "renv.lock":
            steps.append("Rscript -e 'renv::restore(prompt=FALSE)'")
        elif lowered == "description":
            steps.append("Rscript -e 'remotes::install_deps(dependencies=TRUE, upgrade=\"never\")'")
        elif lowered.startswith("pyproject"):
            steps.append("pip install --no-input .")
    if not steps:
        # A specification bioAF cannot install from is not a specification that failed to install.
        return f"echo '{INSTALL_MARKER} {' '.join(paths)} unsupported: bioAF has no installer for this specification'"
    joined = " && ".join(steps)
    return (
        f"if {{ {joined}; }} > /tmp/bioaf-install.log 2>&1; then "
        f"echo '{INSTALL_MARKER} {' '.join(paths)} ok'; "
        f"else echo \"{INSTALL_MARKER} {' '.join(paths)} failed: $(tail -c 800 /tmp/bioaf-install.log | tr '\\n' ' ')\"; fi; "
        "cat /tmp/bioaf-install.log"
    )


def _python_check(modules: list[str], *, declared: dict[str, str | None], interfaces: list[str]) -> str:
    """Import each module on its own, compare its version, and look for the symbols the source uses.

    plan_8_7 stage 3: "Validate declared versions and necessary interfaces under the named
    environment; loading the package root does not verify a symbol used by the source." Three
    observations, printed separately, so a missing symbol is not read as a failed import and a version
    nobody could read is not read as a version that matched.
    """
    return (
        "import importlib, sys\n"
        f"modules = {sorted(modules)!r}\n"
        f"declared = {dict(sorted(declared.items()))!r}\n"
        f"interfaces = {sorted(interfaces)!r}\n"
        "failed = []\n"
        "loaded = {}\n"
        "for name in modules:\n"
        "    try:\n"
        "        loaded[name] = importlib.import_module(name)\n"
        f"        print('{LOAD_MARKER} ' + name + ' ok', flush=True)\n"
        "    except BaseException as exc:\n"
        "        failed.append(name)\n"
        f"        print('{LOAD_MARKER} ' + name + ' failed: ' + str(exc), flush=True)\n"
        "for name, want in declared.items():\n"
        "    try:\n"
        "        from importlib import metadata as _md\n"
        "        got = _md.version(name)\n"
        "    except BaseException:\n"
        "        got = None\n"
        "    if got is None:\n"
        f"        print('{VERSION_MARKER} ' + name + ' declared=' + str(want) + ' observed=none unknown', flush=True)\n"
        "    elif want and got != want:\n"
        f"        print('{VERSION_MARKER} ' + name + ' declared=' + str(want) + ' observed=' + got + ' mismatch',"
        " flush=True)\n"
        "    else:\n"
        f"        print('{VERSION_MARKER} ' + name + ' declared=' + str(want) + ' observed=' + got + ' ok',"
        " flush=True)\n"
        "for path in interfaces:\n"
        "    root = path.split('.')[0]\n"
        "    if root not in loaded:\n"
        f"        print('{INTERFACE_MARKER} ' + path + ' unchecked', flush=True)\n"
        "        continue\n"
        "    target, missing = loaded[root], False\n"
        "    for part in path.split('.')[1:]:\n"
        "        try:\n"
        "            target = getattr(target, part)\n"
        "        except BaseException:\n"
        "            try:\n"
        "                target = importlib.import_module(root + '.' + part)\n"
        "            except BaseException:\n"
        "                missing = True\n"
        "                break\n"
        f"    print('{INTERFACE_MARKER} ' + path + (' missing' if missing else ' ok'), flush=True)\n"
        f"print('{RESOLVE_MARKER} ' + ('failed: ' + ', '.join(failed) if failed else 'ok'), flush=True)\n"
        "sys.exit(1 if failed else 0)\n"
    )


def _r_check(packages: list[str], *, declared: dict[str, str | None]) -> str:
    """Attach each package on its own and compare its version. ``requireNamespace`` loads without
    attaching, which is the narrower thing to ask and still answers whether the environment holds it."""
    wanted = ", ".join(f"{name!r} = {(version or 'NA')!r}" for name, version in sorted(declared.items()))
    return (
        f"packages <- c({', '.join(repr(p) for p in sorted(packages))})\n"
        f"declared <- list({wanted})\n"
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
        "for (name in names(declared)) {\n"
        "  want <- declared[[name]]\n"
        "  got <- tryCatch(as.character(utils::packageVersion(name)), error = function(e) NA_character_)\n"
        "  verdict <- if (is.na(got)) 'unknown' else if (!is.na(want) && got != want) 'mismatch' else 'ok'\n"
        f"  cat('{VERSION_MARKER} ', name, ' declared=', ifelse(is.na(want), 'none', want), ' observed=',"
        " ifelse(is.na(got), 'none', got), ' ', verdict, '\\n', sep = '')\n"
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


def _interfaces(sources: list[dict], language: str) -> list[str]:
    """The dotted attributes the source reaches for on an imported module.

    ``scanpy.pp.pca(...)`` needs `scanpy.pp` to exist, and importing `scanpy` does not establish that.
    Only the names bound to an import are followed: a local object's attribute says nothing about any
    package's interface.
    """
    if language != "python":
        return []
    import ast

    found: set[str] = set()
    for source in sources:
        try:
            tree = ast.parse(str(source.get("text") or ""))
        except SyntaxError:
            continue
        imported: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[(alias.asname or alias.name).split(".")[0]] = alias.name.split(".")[0]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            parts: list[str] = []
            current: ast.expr = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if not isinstance(current, ast.Name) or current.id not in imported:
                continue
            parts.reverse()
            # One level past the root: `scanpy.pp` is the interface a reader can check for, and
            # `scanpy.pp.pca` would be a claim about a function's own attributes.
            found.add(f"{imported[current.id]}.{parts[0]}")
    return sorted(found)


def check_script(*, sources: list[dict] | None, manifests: list[dict] | None) -> str:
    """The script the isolated run executes: install what the paper declared, then read what it built.

    Four phases in order, each reporting its own marker (plan_8_7 stage 3): install the paper's
    declared dependencies, load what the source imports, compare the declared versions with the
    installed ones, and look for the interfaces the source uses. Nothing aborts: a step that fails
    reports and the next one runs, because a repository whose environment will not build is a real
    finding and it is only a finding if the log survives to be read.

    It never runs the paper's own script. Loading a module executes that module's top level, which is
    what makes this an isolated run at all, but running the analysis is a different question with a
    different cost and a different approval.
    """
    request = environment_check_request(sources=sources, manifests=manifests)
    language = request["language"]
    supplied = [s for s in sources or [] if str(s.get("language") or "").lower() == language]
    held = [m for m in manifests or [] if isinstance(m, dict) and m.get("path")]
    required = _required(supplied, language)
    from app.services.validation_code_checks import _all_declared

    declared = {name: version for name, version in _all_declared(held).items() if name in {r.lower() for r in required}}
    runtime = (
        _python_check(required, declared=declared, interfaces=_interfaces(supplied, language))
        if language == "python"
        else _r_check(required, declared=declared)
    )
    return "\n".join(
        [
            "#!/bin/sh",
            "set +e",
            _install_step(held, language),
            f"cat <<'BIOAF_CHECK_EOF' > /tmp/bioaf_runtime_check.{'py' if language == 'python' else 'R'}",
            runtime,
            "BIOAF_CHECK_EOF",
            f"{'python' if language == 'python' else 'Rscript'} /tmp/bioaf_runtime_check."
            f"{'py' if language == 'python' else 'R'}",
            "exit $?",
        ]
    )


_ENTRY_POINT = {"python": "bioaf_environment_check.sh", "r": "bioaf_environment_check.sh"}


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


# What an installation failure tells you about, read from the message the installer printed. The old
# rule counted missing packages, which plan_8_7 stage 3 replaces: "Attribute failure from observed
# conditions, not the number of missing packages."
#
# bioAF's side is anything about reaching the index, the runner's own filesystem or its permissions.
_BIOAF_INSTALL_FAILURE = re.compile(
    r"connection refused|connection reset|could not fetch url|temporary failure in name resolution"
    r"|network is unreachable|proxy|timed out|read timed out|permission denied|no space left"
    r"|ssl\w*error|certificate verify failed|503|502|504|429",
    re.I,
)
# The paper's side is the dependency specification itself not resolving.
_PAPER_INSTALL_FAILURE = re.compile(
    r"no matching distribution|could not find a version|conflict|incompatible|requires .* but"
    r"|no such package|is not available for|package .* (?:not|is not) available|unable to install",
    re.I,
)

BIOAF = "bioaf"
PAPER = "paper"
UNATTRIBUTED = "unattributed"


def _attribute(reason: str) -> str:
    """Whose condition produced this installation failure, from what the installer actually said."""
    if _BIOAF_INSTALL_FAILURE.search(reason):
        return BIOAF
    if _PAPER_INSTALL_FAILURE.search(reason):
        return PAPER
    return UNATTRIBUTED


def _marked(lines: list[str], marker: str) -> list[str]:
    return [line[len(marker) :].strip() for line in lines if line.startswith(marker)]


def outcome_from_run(*, exit_code: int | None, transcript: str, environment: str, ref: str) -> dict:
    """What an isolated run established, read from its own markers, as separate observations.

    plan_8_4 section 3.4: a run that said nothing establishes NOTHING. A pod killed for memory, a
    timeout, an image that could not start are all bioAF's limitations, and reading them as a paper
    whose code will not load is exactly the confusion this rubric exists to prevent.

    plan_8_7 stage 3 adds three rules:

    - the installation is its own observation and SURVIVES a successful import. A package that happens
      to be in the base image says nothing about an environment that could not be built.
    - versions and interfaces are their own observations too. Loading a package root does not verify a
      symbol the source uses from it, and a version nobody could read is not a version that matched.
    - the cause comes from the observed condition. Two dependencies that fail to import in an
      environment that BUILT is a fact about the paper; the same two with nothing installed is a fact
      about whichever image this install configures.
    """
    lines = [line.strip() for line in (transcript or "").splitlines() if line.strip()]
    installs = _marked(lines, INSTALL_MARKER)
    loads = _marked(lines, LOAD_MARKER)
    versions = _marked(lines, VERSION_MARKER)
    interfaces = _marked(lines, INTERFACE_MARKER)
    resolves = _marked(lines, RESOLVE_MARKER)
    if not (installs or loads or versions or interfaces or resolves):
        return {}
    found: dict = {}
    where = {"environment": environment, "ref": ref}

    installed = None
    for answer in installs:
        if answer == "none":
            found["installation"] = {
                "status": "not_performed",
                "reason": "bioAF holds no dependency specification for this analysis, so nothing was installed and "
                "the environment is whichever image this install configures",
                **where,
            }
            installed = False
        elif " failed:" in answer:
            reason = answer.partition(" failed:")[2].strip()
            found["installation"] = {
                "status": "failed",
                "reason": reason,
                "attributed_to": _attribute(reason),
                **where,
            }
            installed = False
        elif " unsupported:" in answer:
            found["installation"] = {
                "status": "unsupported",
                "reason": answer.partition(" unsupported:")[2].strip(),
                "attributed_to": BIOAF,
                **where,
            }
            installed = False
        elif answer.endswith(" ok"):
            found["installation"] = {"status": "succeeded", **where}
            installed = True

    broken = [answer for answer in loads if " failed:" in answer]
    if loads:
        if not broken and installed is True:
            found["load"] = {"status": "succeeded", **where}
        elif not broken:
            # It imported, and not in the environment the paper declared. That is a true observation
            # about bioAF's base image and it does not establish C1.B.
            found["load"] = {
                "status": "inconclusive",
                "reason": "every module the source imports was found, and in an environment bioAF did not build "
                "from this paper's own specification, so it establishes nothing about the environment it declared",
                **where,
            }
        elif installed is True:
            found["load"] = {"status": "failed", "reason": "; ".join(broken), **where}
        else:
            # Nothing was installed, or the installation failed, so "missing" is a fact about the image.
            found["load"] = {
                "status": "inconclusive",
                "reason": "the declared environment was not built ("
                + str((found.get("installation") or {}).get("reason") or "no installation was performed")
                + "), so a module that is absent is absent from bioAF's own image",
                **where,
            }

    mismatched = [answer for answer in versions if answer.endswith(" mismatch")]
    unknown = [answer for answer in versions if answer.endswith(" unknown")]
    if versions:
        if mismatched:
            found["versions"] = {"status": "mismatch", "reason": "; ".join(mismatched), **where}
        elif unknown:
            found["versions"] = {"status": "unknown", "reason": "; ".join(unknown), **where}
        else:
            found["versions"] = {"status": "ok", **where}

    missing = [answer.rpartition(" ")[0] for answer in interfaces if answer.endswith(" missing")]
    unchecked = [answer.rpartition(" ")[0] for answer in interfaces if answer.endswith(" unchecked")]
    if interfaces:
        if missing:
            found["interfaces"] = {"status": "missing", "reason": ", ".join(missing), **where}
        elif unchecked:
            found["interfaces"] = {
                "status": "unchecked",
                "reason": ", ".join(unchecked) + " could not be checked, because their module was not loaded",
                **where,
            }
        else:
            found["interfaces"] = {"status": "ok", **where}

    # C2.B is about the dependency versions and interfaces resolving TOGETHER, in the environment the
    # paper declared. An installation that failed on the paper's own specification settles it; one that
    # failed on bioAF's side settles nothing.
    install = found.get("installation") or {}
    if install.get("status") in ("failed", "unsupported"):
        if install.get("attributed_to") == PAPER:
            found["dependency_resolution"] = {
                "status": "failed",
                "reason": install.get("reason"),
                **where,
            }
        return found
    if install.get("status") == "not_performed":
        return found
    for answer in resolves:
        if answer.startswith("failed"):
            found["dependency_resolution"] = {
                "status": "failed",
                "reason": answer.partition(":")[2].strip() or answer,
                **where,
            }
        elif answer == "ok":
            # The resolve marker is the LAST thing the check prints, so its presence is what says the
            # run reached the end. The exit status is not consulted here: the script exits non-zero
            # when it found a problem, and a problem it named is exactly what these branches read.
            if mismatched or unknown:
                found["dependency_resolution"] = {
                    "status": "inconclusive",
                    "reason": "the modules loaded and their versions did not match what the paper declared: "
                    + "; ".join(mismatched or unknown),
                    **where,
                }
            elif missing:
                found["dependency_resolution"] = {
                    "status": "failed",
                    "reason": "the environment holds the packages and not the interfaces this source uses: "
                    + ", ".join(missing),
                    **where,
                }
            else:
                found["dependency_resolution"] = {"status": "succeeded", **where}
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


def effective_settings(*, request: dict, compute, timeout_seconds: int) -> dict:
    """What the job actually got, beside what was asked for.

    plan_8_7 stage 3: "Record both the requested and effective settings; refuse an unsupported execution
    specification rather than recording unenforced settings as if applied." Read off the compute session
    the executor created, which is what the adapter builds the pod from, so a profile the executor
    substituted is visible instead of the one the request asked for.
    """
    limits = (request or {}).get("limits") or {}
    cpu = str(getattr(compute, "cpu_cores", "") or "")
    memory = f"{getattr(compute, 'memory_gb', '') or ''}Gi"
    profile = str(getattr(compute, "resource_profile", "") or "")
    differences = []
    if profile and profile != request.get("resource_profile"):
        differences.append(f"resource profile {profile} where {request.get('resource_profile')} was requested")
    if cpu and cpu != str(limits.get("cpu") or ""):
        differences.append(f"{cpu} cpu where {limits.get('cpu')} was requested")
    if memory and memory != str(limits.get("memory") or ""):
        differences.append(f"{memory} memory where {limits.get('memory')} was requested")
    if int(timeout_seconds) != int(limits.get("timeout_seconds") or 0):
        differences.append(f"a {timeout_seconds}s timeout where {limits.get('timeout_seconds')}s was requested")
    return {
        "resource_profile": profile or None,
        "cpu": cpu or None,
        "memory": memory if cpu else None,
        "timeout_seconds": int(timeout_seconds),
        "network": limits.get("network"),
        "filesystem": limits.get("filesystem"),
        "applied": not differences,
        "reason": ("the executor ran this with " + "; ".join(differences)) if differences else None,
    }


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
    # Refused before anything is staged: an unsupported specification is not a run whose settings can
    # be recorded, and recording one would be recording a setting nothing applied.
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
    timeout_seconds = int(request["limits"]["timeout_seconds"])
    compute = await NotebookExecutionService.execute_fetched_code(
        session,
        org_id=study.organization_id,
        user_id=user_id,
        code_uri=staged["code_uri"],
        entry_point=staged["entry_point"],
        arguments="",
        input_file_ids=[],
        experiment_id=getattr(study, "experiment_id", None),
        # plan_8_7 stage 3: the recorded contract, passed to the executor rather than described beside
        # it. These two arguments are what make the cpu, the memory and the timeout on the record true.
        resource_profile=request["resource_profile"],
        timeout_seconds=timeout_seconds,
    )
    record = {
        "status": "running",
        "session_id": compute.id,
        "language": staged["language"],
        "runtime": request["runtime"],
        "files": staged["files"],
        "establishes": request["establishes"],
        "limits": request["limits"],
        "phases": request["phases"],
        "working_directory": request["working_directory"],
        "resource_profile": request["resource_profile"],
        # What this result is a result ABOUT: the revision of the source and the manifests it ran on.
        "source_digest": request["source_digest"],
        "manifest_digest": request["manifest_digest"],
        # What the job actually got, so an unenforced setting cannot read as an applied one.
        "effective": effective_settings(request=request, compute=compute, timeout_seconds=timeout_seconds),
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
