"""plan_8_4 milestone B: rubric v3's code section, assessed from the source a paper supplied.

**Static inspection only, by a real parser.** A regular expression that thinks it knows a language is
not a syntax check, and this module refuses to pretend: a language with no declared parser leaves its
obligations undetermined and says so. Loading, installing, compiling and smoke execution are none of
this module's business either; they run untrusted code and belong behind the existing isolated
execution path and its approval, so they are verified only from a RECORDED result of that path.

**What must never cost a point** (section 3.4): style, formatting, lint preferences,
deprecated-but-functional syntax, the age of a tool, and a newer version merely producing different
output. Pinning an older version can be exactly what faithful reproduction requires. A failure names a
specific unmet obligation, its cause and its impact, or it is not a failure.

Pure: no database, no model, no network, and it never imports or executes the source it reads.
"""

from __future__ import annotations

import ast
import builtins
import re
import sys

from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

MEASUREMENT = "measurement"

# The explicit language capability declaration section 6.2 asks for. A language absent from this is a
# language bioAF cannot parse, which makes its syntax UNKNOWN rather than wrong.
# plan_8_5 section 3.5: R joins it, read by bioAF's own R parser. Nothing here executes either one.
SUPPORTED_LANGUAGES = ("python", "r")

_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}

# An absolute path under a user's home or desktop is a path that exists on one machine. It is not a
# style preference: the supplied analysis cannot read it anywhere else. plan_8_5 section 3.5: a
# home-relative path (``~/Dropbox/...``) is the same defect and was not being read as one.
_PERSONAL_PATH = re.compile(r"^(~/|/Users/|/home/|/Volumes/|[A-Za-z]:\\\\Users\\\\)")

# A ground that is only "this is old". Section 3.4 forbids deducting for tool age, so a defect record
# whose evidence says nothing else is not an established defect.
_AGE_ONLY = re.compile(r"\b(old|older|outdated|ancient|years old|deprecated|unmaintained|legacy)\b", re.I)

_DYNAMIC_IMPORT = ("importlib", "__import__", "pkgutil", "imp")


def _finding(outcome: str, rationale: str, *, scope: str, **extra) -> dict:
    return {"outcome": outcome, "rationale": rationale, "scope": scope, "method": MEASUREMENT, **extra}


def _open(rationale: str, *, scope: str, next_action: str, **extra) -> dict:
    return _finding(UNDETERMINED, rationale, scope=scope, next_action=next_action, **extra)


def _language(source: dict) -> str:
    return str(source.get("language") or "").strip().lower()


def _segments(source: dict) -> list[dict]:
    """The units a source is parsed in: its chunks where it has them, else the whole file.

    plan_8_5 section 3.5: a document's chunk is its own unit with its own place, so a diagnostic
    names the chunk a reader can find rather than a line of a file nobody supplied.
    """
    segments = [s for s in source.get("segments") or [] if isinstance(s, dict)]
    if segments:
        return segments
    return [{"code": source.get("text") or "", "line": 1, "label": None}]


def _where(source: dict, segment: dict, line: int) -> str:
    """Where a diagnostic is, in the words of the thing that was supplied."""
    place = f"line {int(segment.get('line') or 1) + max(0, line - 1)}"
    label = segment.get("label")
    return f"{source.get('path')} ({label}, {place})" if label else f"{source.get('path')} {place}"


def _parse_r_source(source: dict) -> tuple[dict | None, dict | None]:
    """An R source read by bioAF's R parser: (reading, refusal). One of the two is None.

    A refusal is the source's own defect. A construct the parser cannot read is not a refusal: it
    returns neither, and the caller records bioAF's limit.
    """
    from app.services.r_parser import CANNOT_ESTABLISH, PARSED, parse_r, read_r

    unreadable = None
    for segment in _segments(source):
        result = parse_r(segment.get("code") or "")
        if result["status"] == PARSED:
            continue
        if result["status"] == CANNOT_ESTABLISH:
            unreadable = unreadable or {
                "source": source,
                "where": _where(source, segment, result["line"]),
                "message": result["message"],
                "parser": result["parser"],
            }
            continue
        return None, {
            "source": source,
            "where": _where(source, segment, result["line"]),
            "line": int(segment.get("line") or 1) + max(0, result["line"] - 1),
            "message": result["message"],
            "parser": result["parser"],
        }
    if unreadable is not None:
        return None, None
    return {
        "source": source,
        "language": "r",
        "parser": "bioAF R parser",
        "r": read_r("\n".join(str(s.get("code") or "") for s in _segments(source))),
    }, None


def _parsed(sources: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Each supported source read, the ones that are broken, the ones in a language bioAF cannot
    parse, and the ones whose language it declares and could not read this time."""
    readings: list[dict] = []
    broken: list[dict] = []
    unsupported: list[dict] = []
    unreadable: list[dict] = []
    for source in sources or []:
        language = _language(source)
        if language not in SUPPORTED_LANGUAGES:
            unsupported.append(source)
            continue
        if language == "r":
            reading, refusal = _parse_r_source(source)
            if reading is not None:
                readings.append(reading)
            elif refusal is not None:
                broken.append(refusal)
            else:
                unreadable.append(source)
            continue
        try:
            tree = ast.parse(source.get("text") or "", filename=source.get("path") or "<source>")
        except SyntaxError as exc:
            broken.append(
                {
                    "source": source,
                    "where": f"{source.get('path')} line {exc.lineno}",
                    "line": exc.lineno,
                    "message": exc.msg,
                    "parser": "python ast",
                }
            )
            continue
        readings.append({"source": source, "language": "python", "parser": "python ast", "tree": tree})
    return readings, broken, unsupported, unreadable


def _trees(readings: list[dict]) -> list[tuple[dict, ast.Module]]:
    """The Python readings, in the (source, tree) shape the Python checks work in."""
    return [(r["source"], r["tree"]) for r in readings if r["language"] == "python"]


def _scope(sources: list[dict]) -> str:
    names = [str(s.get("path") or "an unnamed file") for s in sources or []]
    return ", ".join(names) or "no source in hand"


def assess_code(
    *,
    sources: list[dict] | None,
    manifests: list[dict] | None = None,
    defects: list[dict] | None = None,
    execution: dict | None = None,
) -> dict:
    """C1 to C5 from the supplied source, its manifests, a recorded fitness review and a recorded run.

    ``sources`` are ``{"path", "language", "text"}``. ``manifests`` are dependency and environment
    specifications as text. ``defects`` are recorded evidence-backed reviews. ``execution`` holds the
    results of the isolated execution path, which is the only thing that verifies C1.B and C2.B.
    """
    supplied = [s for s in sources or [] if isinstance(s, dict)]
    # plan_8_4 section 3.3: code bioAF generated to stand in for the authors' establishes nothing about
    # the authors' code. It is not source the paper supplied, and this section is about what it did.
    generated = [s for s in supplied if s.get("generated")]
    sources = [s for s in supplied if not s.get("generated")]
    manifests = [m for m in manifests or [] if isinstance(m, dict)]
    if not sources and generated:
        stood_in = (
            "the only source in hand was generated by bioAF to stand in for the authors'; it establishes "
            "nothing about the code the paper supplied"
        )
        return {
            leaf: _open(
                stood_in,
                scope=_scope(generated),
                next_action="retrieve the authors' own source",
            )
            for leaf in ("C1.A", "C1.B", "C2.A", "C2.B", "C3.A", "C3.B", "C4.A", "C4.B", "C5.A", "C5.B")
        }
    if not sources:
        held = "bioAF holds no source for this paper's analysis"
        return {
            leaf: _open(held, scope="no source in hand", next_action="retrieve the paper's supplied code")
            for leaf in ("C1.A", "C1.B", "C2.A", "C2.B", "C3.A", "C3.B", "C4.A", "C4.B", "C5.A", "C5.B")
        }
    readings, broken, unsupported, unreadable = _parsed(sources)
    return {
        **_syntax(readings, broken, unsupported, unreadable),
        **_build(sources, execution),
        **_imports(sources, readings, manifests, unsupported, unreadable),
        **_resolution(sources, execution),
        **_environment(sources, readings, manifests),
        **_completeness(sources, readings, unsupported, unreadable),
        **_fitness(sources, readings, defects),
    }


def _syntax(readings, broken, unsupported, unreadable) -> dict:
    if broken:
        refusal = broken[0]
        source = refusal["source"]
        return {
            "C1.A": _finding(
                FAILED,
                f"{refusal['where']} does not parse as {source.get('language')}: {refusal['message']}",
                scope=_scope([r["source"] for r in readings] + [b["source"] for b in broken]),
                impact="the supplied analysis cannot run as written, so nothing downstream of it can be reproduced",
                evidence={
                    "path": source.get("path"),
                    "line": refusal.get("line"),
                    "message": refusal["message"],
                    "parser": refusal["parser"],
                },
            )
        }
    if unreadable:
        # plan_8_5 section 3.5: bioAF's parser met something it does not implement. That is bioAF's
        # limit, and a paper is never told its code is broken by a reader that could not read it.
        return {
            "C1.A": _open(
                f"bioAF's parser could not read {_scope(unreadable)}, so whether it parses is unknown",
                scope=_scope(unreadable),
                next_action="extend the parser to the constructs this source uses",
                capability_limit=True,
            )
        }
    if readings:
        return {
            "C1.A": _finding(
                VERIFIED,
                f"every supplied source parses under its declared language ({_scope([r['source'] for r in readings])})",
                scope=_scope([r["source"] for r in readings]),
                evidence={
                    "parser": ", ".join(sorted({r["parser"] for r in readings})),
                    "files": [r["source"].get("path") for r in readings],
                },
            )
        }
    languages = sorted({str(s.get("language") or "unknown") for s in unsupported})
    return {
        "C1.A": _open(
            f"bioAF holds no parser for {', '.join(languages)}, so whether this source parses is unknown",
            scope=_scope(unsupported),
            next_action=f"add a declared parser for {', '.join(languages)}",
            capability_limit=True,
        )
    }


def _build(sources, execution) -> dict:
    load = (execution or {}).get("load") or {}
    if load.get("status") == "succeeded":
        return {
            "C1.B": _finding(
                VERIFIED,
                f"the supplied source loads in {load.get('environment') or 'the declared environment'}",
                scope=_scope(sources),
                evidence={"ref": load.get("ref"), "environment": load.get("environment")},
            )
        }
    if load.get("status") == "failed":
        return {
            "C1.B": _finding(
                FAILED,
                f"loading the supplied source in the declared environment failed: {load.get('reason')}",
                scope=_scope(sources),
                impact="the analysis cannot be started from what was supplied",
                evidence={"ref": load.get("ref")},
            )
        }
    return {
        "C1.B": _open(
            "loading the supplied source executes it, so bioAF runs it only through its isolated "
            "execution path, under the approval that path requires; no such run is recorded",
            scope=_scope(sources),
            next_action="approve an isolated load of the supplied source",
        )
    }


def _declared_packages(manifests) -> dict[str, str | None]:
    """Each package a manifest declares, and the version it pins, or None for unpinned."""
    declared: dict[str, str | None] = {}
    for manifest in manifests:
        for line in str(manifest.get("text") or "").splitlines():
            entry = line.split("#", 1)[0].strip().lstrip("-").strip()
            if not entry or entry.endswith(":") or entry.startswith(("FROM", "RUN", "COPY", "WORKDIR", "ENV")):
                continue
            match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:([=<>!~]=?)\s*([A-Za-z0-9_.\-]+))?", entry)
            if not match:
                continue
            name = match.group(1).lower().replace("_", "-")
            pinned = match.group(3) if match.group(2) in ("==", "=") else None
            if name not in declared or pinned:
                declared[name] = pinned
    return declared


# What a package is called when imported, where that differs from what it is called when installed.
_IMPORT_NAMES = {"sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml"}


def _imported(trees) -> tuple[set[str], bool]:
    """Every top-level module the source imports plainly, and whether it also imports dynamically."""
    modules: set[str] = set()
    dynamic = False
    for _source, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # a relative import names a sibling of this file, not a package
                    continue
                if node.module:
                    modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
                dynamic = True
    if modules & set(_DYNAMIC_IMPORT):
        dynamic = True
    return modules, dynamic


def _local_modules(sources) -> set[str]:
    return {str(s.get("path") or "").rsplit("/", 1)[-1].removesuffix(".py") for s in sources}


def _r_requirements(sources, readings) -> dict | None:
    """C2.A for the R sources: what they attach, and whether what they source is in hand.

    plan_8_5 section 3.5. R declares its dependencies in the source itself, so ``library(DESeq2)``
    IS the declaration that makes the symbols available. What can be established as missing is a file
    the source reads in as code and that nobody supplied: a relative path names a file the authors
    meant to supply beside it. An absolute path on their own machine is a different defect, and it is
    recorded once, against the coherence obligation that owns it.
    """
    r_readings = [r for r in readings if r["language"] == "r"]
    if not r_readings:
        return None
    supplied = {str(s.get("path") or "").rsplit("/", 1)[-1].lower() for s in sources}
    attached: set[str] = set()
    missing: list[str] = []
    dynamic = False
    for reading in r_readings:
        found = reading["r"]
        attached |= set(found["packages"]) | set(found["namespaced"])
        dynamic = dynamic or bool(found["dynamic"])
        for path in found["sourced"]:
            if _PERSONAL_PATH.match(path) or path.startswith("/"):
                continue
            if path.rsplit("/", 1)[-1].lower() not in supplied and path not in missing:
                missing.append(path)
    if missing:
        return _finding(
            FAILED,
            f"the source reads in {', '.join(missing)} as code, and nothing supplied provides it",
            scope=_scope([r["source"] for r in r_readings]),
            impact="the analysis stops where it sources that file, so nothing after it was run from what was supplied",
            evidence={"missing": missing, "supplied": sorted(supplied)},
        )
    if dynamic:
        return _open(
            "the source names a package in a variable, so what it actually requires cannot be established "
            "by reading it",
            scope=_scope([r["source"] for r in r_readings]),
            next_action="approve an isolated load, which is what establishes the real requirement set",
        )
    if not attached:
        return _open(
            "the source attaches no package and names none, so what it requires is not stated in it",
            scope=_scope([r["source"] for r in r_readings]),
            next_action="retrieve the script that attaches this analysis's packages",
        )
    return _finding(
        VERIFIED,
        "the source attaches or namespaces every package it uses, and every file it reads in as code is in hand",
        scope=_scope([r["source"] for r in r_readings]),
        evidence={"packages": sorted(attached)},
    )


def _imports(sources, readings, manifests, unsupported, unreadable) -> dict:
    trees = _trees(readings)
    r_answer = _r_requirements(sources, readings)
    if not trees:
        if r_answer is not None:
            return {"C2.A": r_answer}
        return {
            "C2.A": _open(
                "bioAF holds no parser for this source, so what it requires is unknown",
                scope=_scope(unsupported + unreadable),
                next_action="add a declared parser for this language",
                capability_limit=True,
            )
        }
    modules, dynamic = _imported(trees)
    declared = _declared_packages(manifests)
    local = _local_modules(sources)
    missing = sorted(
        module
        for module in modules
        if module not in _STDLIB
        and module not in local
        and module.lower().replace("_", "-") not in declared
        and _IMPORT_NAMES.get(module, "").lower() not in declared
        and module not in _DYNAMIC_IMPORT
    )
    if dynamic:
        python_answer = _open(
            "the source imports dynamically, so what it actually requires cannot be established by "
            "reading it; a name absent from the manifests may still be imported at run time",
            scope=_scope(sources),
            next_action="approve an isolated load, which is what establishes the real requirement set",
        )
    elif missing:
        python_answer = _finding(
            FAILED,
            f"the source imports {', '.join(missing)}, which nothing supplied declares and which is "
            "neither standard library nor another supplied file",
            scope=_scope(sources),
            impact="the analysis would stop at the import, so nothing it computes can be reproduced",
            evidence={"missing": missing, "declared": sorted(declared)},
        )
    else:
        python_answer = _finding(
            VERIFIED,
            "every module the source imports is standard library, another supplied file, or declared in "
            "a supplied manifest",
            scope=_scope(sources),
            evidence={"imports": sorted(modules), "declared": sorted(declared)},
        )
    if r_answer is None:
        return {"C2.A": python_answer}
    # Two languages in one analysis: a demonstrated failure in either one is the answer, and an open
    # question in either leaves the obligation open.
    for answer in (python_answer, r_answer):
        if answer["outcome"] == FAILED:
            return {"C2.A": answer}
    for answer in (python_answer, r_answer):
        if answer["outcome"] == UNDETERMINED:
            return {"C2.A": answer}
    return {"C2.A": python_answer}


def _resolution(sources, execution) -> dict:
    resolution = (execution or {}).get("dependency_resolution") or {}
    if resolution.get("status") == "succeeded":
        return {
            "C2.B": _finding(
                VERIFIED,
                "the declared dependency versions and interfaces resolved together in a bounded environment check",
                scope=_scope(sources),
                evidence={"ref": resolution.get("ref")},
            )
        }
    if resolution.get("status") == "failed":
        return {
            "C2.B": _finding(
                FAILED,
                f"the declared dependency versions do not resolve together: {resolution.get('reason')}",
                scope=_scope(sources),
                impact="the environment the paper describes cannot be built as described",
                evidence={"ref": resolution.get("ref")},
            )
        }
    return {
        "C2.B": _open(
            "resolving dependency versions installs packages, so bioAF does it only through its isolated "
            "execution path, under the approval that path requires; no such run is recorded",
            scope=_scope(sources),
            next_action="approve a bounded environment check",
        )
    }


_RUNTIME_VERSION = re.compile(r"\b(?:python|r|julia)[:=\s/-]*\d+\.\d+", re.I)


def _renv_packages(manifest: dict) -> dict[str, str | None]:
    """The packages an ``renv.lock`` pins, from its own JSON. Empty where it cannot be read."""
    import json

    try:
        data = json.loads(str(manifest.get("text") or ""))
    except (TypeError, ValueError):
        return {}
    packages = data.get("Packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        return {}
    return {
        str(name).lower(): (str(entry.get("Version")) if isinstance(entry, dict) and entry.get("Version") else None)
        for name, entry in packages.items()
    }


def _description_packages(manifest: dict) -> dict[str, str | None]:
    """The packages an R ``DESCRIPTION`` declares under Imports/Depends/Suggests, and their versions."""
    declared: dict[str, str | None] = {}
    section = None
    for raw in str(manifest.get("text") or "").splitlines():
        header, sep, rest = raw.partition(":")
        if sep and not raw.startswith((" ", "\t")):
            section = header.strip().lower()
            rest = rest.strip()
        else:
            rest = raw.strip()
        if section not in ("imports", "depends", "suggests") or not rest:
            continue
        for entry in rest.split(","):
            entry = entry.strip().rstrip(",")
            if not entry:
                continue
            name, _, version = entry.partition("(")
            name = name.strip().lower()
            if not name or name == "r":
                continue
            pinned = None
            if version:
                digits = re.search(r"==\s*([0-9][0-9.\-]*)", version)
                pinned = digits.group(1) if digits else None
            declared[name] = pinned
    return declared


def _all_declared(manifests) -> dict[str, str | None]:
    """Every package the supplied manifests declare, whichever language's manifest declared it."""
    declared = dict(_declared_packages(manifests))
    for manifest in manifests:
        name = str(manifest.get("path") or "").rsplit("/", 1)[-1].lower()
        if name == "renv.lock":
            declared.update(_renv_packages(manifest))
        elif name == "description":
            declared.update(_description_packages(manifest))
    return declared


_R_RUNTIME = re.compile(r'"R"\s*:\s*\{[^}]*"Version"\s*:\s*"\d+\.\d+', re.S)


def _environment(sources, readings, manifests) -> dict:
    trees = _trees(readings)
    declared = _all_declared(manifests)
    if not manifests:
        held = "bioAF holds no dependency or environment specification for this analysis"
        return {
            "C3.A": _open(
                held, scope="no manifest in hand", next_action="retrieve the paper's environment specification"
            ),
            "C3.B": _open(
                held, scope="no manifest in hand", next_action="retrieve the paper's environment specification"
            ),
        }
    modules, _dynamic = _imported(trees) if trees else (set(), False)
    used = {
        module.lower().replace("_", "-")
        for module in modules
        if module not in _STDLIB and module not in _local_modules(sources)
    } | {_IMPORT_NAMES[m].lower() for m in modules if m in _IMPORT_NAMES}
    for reading in readings:
        if reading["language"] == "r":
            used |= {p.lower() for p in reading["r"]["packages"]} | {p.lower() for p in reading["r"]["namespaced"]}
    # Only a dependency this analysis ACTUALLY uses is result-sensitive. A general "not everything is
    # pinned" rule is explicitly insufficient (section 3.4).
    unpinned = sorted(name for name, version in declared.items() if name in used and not version)
    # A package keeps the spelling the source uses: "DESeq2" is what a reader looks for, not "deseq2".
    spelling = {
        package.lower(): package
        for reading in readings
        if reading["language"] == "r"
        for package in reading["r"]["packages"] + reading["r"]["namespaced"]
    }
    if unpinned:
        pinned = _finding(
            FAILED,
            f"the supplied environment specification declares {', '.join(spelling.get(n, n) for n in unpinned)} "
            "without a version, and "
            "this analysis uses it, so the versions its results depend on cannot be recovered",
            scope=", ".join(str(m.get("path")) for m in manifests),
            impact="a rerun would resolve a different version and could produce different numbers",
            evidence={"unpinned": unpinned},
        )
    elif used and any(name in used for name in declared):
        pinned = _finding(
            VERIFIED,
            "every dependency this analysis uses is declared at a fixed version",
            scope=", ".join(str(m.get("path")) for m in manifests),
            evidence={"pinned": sorted(name for name in declared if name in used)},
        )
    else:
        pinned = _open(
            "bioAF could not establish which of the declared dependencies this analysis uses",
            scope=", ".join(str(m.get("path")) for m in manifests),
            next_action="add a declared parser for this source's language",
        )
    text = "\n".join(str(m.get("text") or "") for m in manifests)
    if _RUNTIME_VERSION.search(text) or _R_RUNTIME.search(text):
        runtime = _finding(
            VERIFIED,
            "the supplied specification states the runtime it was built against and how to rebuild it",
            scope=", ".join(str(m.get("path")) for m in manifests),
        )
    else:
        runtime = _finding(
            FAILED,
            "the supplied environment specification states no runtime version, so the environment it "
            "describes cannot be reconstructed as it was",
            scope=", ".join(str(m.get("path")) for m in manifests),
            impact="a rebuild picks whatever runtime is current, which is not the one that produced the results",
        )
    return {"C3.A": pinned, "C3.B": runtime}


def _entry_points(trees) -> list[str]:
    found = []
    for source, tree in trees:
        for node in tree.body:
            if isinstance(node, ast.If):
                test = ast.dump(node.test)
                if "__main__" in test or "__name__" in test:
                    found.append(str(source.get("path")))
                    break
            elif isinstance(node, (ast.Expr, ast.Assign, ast.AugAssign)) and not isinstance(
                getattr(node, "value", None), (ast.Constant,)
            ):
                found.append(str(source.get("path")))
                break
    return found


def _bound_names(tree: ast.Module) -> set[str]:
    bound = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The name itself. Its parameters arrive as `ast.arg` nodes in the same walk.
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound |= set(node.names)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return bound


def _completeness(sources, readings, unsupported, unreadable) -> dict:
    trees = _trees(readings)
    if not readings:
        held = "bioAF holds no parser for this source, so what it covers cannot be established"
        return {
            "C4.A": _open(
                held, scope=_scope(unsupported + unreadable), next_action="add a declared parser", capability_limit=True
            ),
            "C4.B": _open(
                held, scope=_scope(unsupported + unreadable), next_action="add a declared parser", capability_limit=True
            ),
        }
    entry = _entry_points(trees)
    # plan_8_5 section 3.5: an R source that does work when it runs states how the analysis starts.
    # A file of function definitions and nothing else does not, in either language.
    entry += [
        str(r["source"].get("path")) for r in readings if r["language"] == "r" and r["r"]["runs"]
    ]
    if entry:
        covers = _finding(
            VERIFIED,
            f"the supplied source has an entry point that runs the analysis ({', '.join(entry)})",
            scope=_scope(sources),
        )
    else:
        covers = _open(
            "the supplied source defines functions and runs none of them, so nothing in it states how the "
            "analysis is started",
            scope=_scope(sources),
            next_action="retrieve the script or notebook that calls this code",
        )
    unbound: list[str] = []
    personal: list[str] = []
    for source, tree in trees:
        bound = _bound_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
                unbound.append(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _PERSONAL_PATH.match(node.value):
                personal.append(node.value)
    for reading in readings:
        if reading["language"] == "r":
            personal += [s for s in reading["r"]["strings"] if _PERSONAL_PATH.match(s)]
    if unbound:
        coherent = _finding(
            FAILED,
            f"the supplied source uses {', '.join(sorted(set(unbound)))}, which nothing in it defines, "
            "imports, or receives as an argument",
            scope=_scope(sources),
            impact="the analysis stops at that line, so what follows it was never run from this source",
            evidence={"unbound": sorted(set(unbound))},
        )
    elif personal:
        coherent = _finding(
            FAILED,
            f"the supplied source reads {', '.join(sorted(set(personal))[:3])}"
            + (f" and {len(set(personal)) - 3} more" if len(set(personal)) > 3 else "")
            + ", a path on one machine rather than an input the analysis is given",
            scope=_scope(sources),
            impact="the analysis cannot find its inputs anywhere but the authors' own computer",
            evidence={"paths": sorted(set(personal))},
        )
    elif trees:
        coherent = _finding(
            VERIFIED,
            "every name the source uses is defined, imported or received, and its inputs are taken rather "
            "than hard-coded to one machine",
            scope=_scope(sources),
        )
    else:
        # bioAF resolves no R name, so "every name is defined" is not established by reading it. The
        # half that IS established (where the inputs come from) found nothing to report.
        coherent = _open(
            "the source takes its inputs rather than hard-coding them to one machine; bioAF resolves no "
            "R name, so whether every name it uses is defined is not established by reading it",
            scope=_scope(sources),
            next_action="approve an isolated load, which is what resolves the names this source uses",
        )
    return {"C4.A": covers, "C4.B": coherent}


def _fitness(sources, readings, defects) -> dict:
    reviews = [d for d in defects or [] if isinstance(d, dict)]
    appropriate = [d for d in reviews if d.get("kind") == "fitness" and d.get("established") and d.get("evidence")]
    if appropriate:
        suits = _finding(
            VERIFIED,
            "an evidence-backed review establishes that "
            + ", ".join(str(d.get("operation")) for d in appropriate)
            + " suits its input and the calculation it is used for",
            scope=", ".join(str(d.get("operation")) for d in appropriate),
            evidence=[d.get("evidence") for d in appropriate],
        )
    else:
        suits = _open(
            "no evidence-backed review of whether the operations this analysis uses suit their inputs has been made",
            scope=_scope(sources),
            next_action="review the operations the analysis uses against their documented behaviour",
        )
    claimed = [d for d in reviews if d.get("kind") == "defect"]
    established = [
        d for d in claimed if d.get("established") and d.get("evidence") and not _age_only(str(d.get("evidence") or ""))
    ]
    age_only = [d for d in claimed if d.get("established") and _age_only(str(d.get("evidence") or ""))]
    if established:
        defect = established[0]
        version = f" at {defect['version']}" if defect.get("version") else ""
        conflicts = _finding(
            FAILED,
            f"a documented defect affects {defect.get('operation')}{version}, which this analysis uses: "
            f"{defect.get('evidence')}",
            scope=str(defect.get("operation")),
            impact=defect.get("impact") or "the operation does not do what the analysis relies on it doing",
            evidence={
                "operation": defect.get("operation"),
                "version": defect.get("version"),
                "source": defect.get("evidence"),
            },
        )
    elif age_only:
        conflicts = _open(
            "the only ground offered for a defect is the tool's age, and age alone is not a defect: pinning "
            "an older version can be exactly what faithful reproduction requires",
            scope=str(age_only[0].get("operation")),
            next_action="cite the version-specific evidence that the operation this analysis uses is affected",
        )
    elif claimed:
        conflicts = _open(
            "a concern was raised about "
            + ", ".join(str(d.get("operation")) for d in claimed)
            + " and it is not established against the version and configuration this analysis uses",
            scope=", ".join(str(d.get("operation")) for d in claimed),
            next_action="retrieve the version-specific evidence, or record that none was found",
        )
    else:
        conflicts = _open(
            "no review of the versions and documented defects of the operations this analysis uses has been made",
            scope=_scope(sources),
            next_action="review the operations against their version-specific documented defects",
        )
    return {"C5.A": suits, "C5.B": conflicts}


def _age_only(evidence: str) -> bool:
    """Whether a defect's only ground is that the tool is old. Section 3.4 forbids deducting for that."""
    return bool(_AGE_ONLY.search(evidence)) and not re.search(r"\b\d+\.\d+", evidence)
