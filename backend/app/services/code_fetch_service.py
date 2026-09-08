"""plan_7 step 16: fetch and pin the authors' code.

Resolution order is the owner's: **the code ARTIFACT first, then the repository.** DESIRED STATE
phase 3 reads "run the code artifact if one exists, else the GitHub repo", and phase 4's checklist
carries a ``Download`` value that nothing could act on while this handled repositories only.

**A dead, private or unpinnable link is an OUTCOME, not a failure of this step.** It is recorded as
``code_unreachable`` and the run carries on; it is a true statement about the paper, and step 17's
vocabulary exists to say exactly that.

**This is where accessibility gets answered.** Step 13 established that a source EXISTS; only an
attempted fetch establishes whether it can be reached. The result is written back per source so the
checklist can say "GitHub, exists, not accessible: repository is private" rather than flattening two
facts into one cell. ``no`` is a 404, a 403 or an unresolvable ref; ``unknown`` is a transport
failure, because a timeout is not evidence of absence.

**Every recorded source is tried before concluding ``code_absent``.** A paper with a dead Zenodo DOI
and a live GitHub mirror has usable code, and the failures of the others are recorded rather than
discarded.

**Searching for an unlinked repository is out of scope.** Capture what the paper states; do not go
hunting. A repository found by searching is not the one the paper published, and attributing a
result to it would be a lie about provenance.

The HTTP boundary is an injectable async callable, matching ``ground_truth_fetch_service``,
``accession_manifest_service`` and ``deposit_inventory_service``, so this is testable without
network exactly as they are.
"""

from __future__ import annotations

import io
import json
import logging
import posixpath
import re
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("bioaf.code_fetch")

Fetcher = Callable[[str], Awaitable[bytes]]

RESOLVED = "resolved"
CODE_ABSENT = "code_absent"
CODE_UNREACHABLE = "code_unreachable"

# Repository hosts we can resolve to a pinned commit. Everything else is fetched as an archive.
_REPO_KINDS = ("github", "gitlab")

# Caps, as step 5 has for deposit downloads. A repository is source code; an archive in the hundreds
# of megabytes is a data drop wearing a repository's clothes, and unpacking it would spend the
# session's disk on something no entry point is going to read.
_MAX_BYTES = 200 * 1024 * 1024
_MAX_FILES = 5000

# What a 404/403 means versus what a dropped connection means. The first is an established absence
# and the second is a discovery failure, and the checklist renders them differently.
_DEFINITE_ABSENCE = ("404", "403", "not found", "gone", "410", "unauthorized", "forbidden")

_GITHUB_REPO_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)
_GITLAB_REPO_RE = re.compile(r"gitlab\.com/([^\s#?]+?)/([^/\s#?]+)", re.IGNORECASE)


@dataclass
class CodeResolution:
    """What happened when we went looking for the authors' code.

    ``outcome`` is one of ``resolved``, ``code_absent``, ``code_unreachable``. The last two are step
    17's vocabulary, because "the paper published nothing" and "the paper published something we
    could not get" are different findings and the report has to be able to say which.
    """

    outcome: str
    kind: str | None = None
    url: str | None = None
    commit_sha: str | None = None
    files: list[dict] = field(default_factory=list)
    archive: bytes | None = None
    reason: str = ""
    attempts: list[dict] = field(default_factory=list)
    # Per source: what an attempted fetch established about reachability, for step 13's record.
    accessibility: dict[str, dict] = field(default_factory=dict)


def safe_member_path(name: str) -> str | None:
    """A normalized archive member path, or None when it tries to escape.

    ``reference_importer``'s tar reader streams members straight to storage keys, which sidesteps
    filesystem traversal but does not sanitize ``../``. This is the first path in bioAF that unpacks
    a THIRD-PARTY archive, so the guard is explicit rather than inherited.
    """
    raw = (name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw.split("/", 1)[0]:
        return None
    normalized = posixpath.normpath(raw)
    if normalized in (".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def _repo_slug(kind: str, url: str) -> tuple[str, str] | None:
    pattern = _GITHUB_REPO_RE if kind == "github" else _GITLAB_REPO_RE
    m = pattern.search(url or "")
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git")


def _accessibility_for(exc: Exception) -> tuple[str, str]:
    """(accessible, reason) from a failed fetch.

    A 404 or a 403 is an established ``no``: the thing is not reachable and asking again will not
    change that. A dropped connection is ``unknown``, because a timeout is not evidence of absence
    and rendering it as one would tell the reader something false about the paper.
    """
    detail = str(exc)
    lowered = detail.lower()
    if any(sig in lowered for sig in _DEFINITE_ABSENCE):
        return "no", detail
    return "unknown", detail


def _unpack(blob: bytes, *, max_bytes: int, max_files: int) -> tuple[list[dict], str | None]:
    """Every file in an archive, as ``{path, size_bytes}``, or (\\[\\], reason) when it cannot be taken.

    Handles the two shapes a published analysis actually arrives in: a gzipped tar (GitHub's
    tarball, and most supplementary archives) and a zip (journal supplements).
    """
    if len(blob) > max_bytes:
        return [], f"the archive is {len(blob)} bytes, larger than the {max_bytes}-byte cap for fetched code"

    names: list[tuple[str, int]] = []
    try:
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                names = [(i.filename, i.file_size) for i in zf.infolist() if not i.is_dir()]
        else:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
                names = [(m.name, m.size) for m in tf.getmembers() if m.isfile()]
    except Exception as exc:  # noqa: BLE001 - an unreadable archive is an outcome, not a crash
        return [], f"the fetched archive could not be read ({exc})"

    if len(names) > max_files:
        return [], f"the archive holds {len(names)} files, more than the {max_files}-file cap for fetched code"

    # A repository tarball wraps everything in `<repo>-<sha>/`. Stripping it keeps entry-point paths
    # readable and stops every path starting with a directory nothing else knows about.
    prefixes = {n.split("/", 1)[0] for n, _ in names if "/" in n}
    strip = prefixes.pop() + "/" if len(prefixes) == 1 and all("/" in n for n, _ in names) else ""

    files: list[dict] = []
    total = 0
    for name, size in names:
        safe = safe_member_path(name)
        if safe is None:
            return [], f"the archive contains a path that escapes its own directory ({name}); it was not unpacked"
        total += size
        if total > max_bytes:
            return [], f"the archive unpacks to more than the {max_bytes}-byte cap for fetched code"
        files.append({"path": safe.removeprefix(strip), "size_bytes": size})
    return files, None


async def _fetch_repository(source: dict, fetch: Fetcher, *, max_bytes: int, max_files: int) -> CodeResolution:
    """Resolve a repository to a pinned commit and fetch that commit's tree.

    A repo that moved is not the one the paper used, so an unpinnable repository is refused BY NAME
    rather than fetched at whatever HEAD happens to be. The provenance report has to name what
    actually ran.
    """
    kind, url = source["kind"], source.get("url") or ""
    slug = _repo_slug(kind, url)
    if not slug:
        return CodeResolution(
            CODE_UNREACHABLE, kind=kind, url=url, reason=f"{url} is not a repository URL this can resolve"
        )
    owner, repo = slug

    if kind == "github":
        ref_url = f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
        tar_url = f"https://api.github.com/repos/{owner}/{repo}/tarball/"
    else:
        ref_url = f"https://gitlab.com/api/v4/projects/{owner}%2F{repo}/repository/commits/HEAD"
        tar_url = f"https://gitlab.com/{owner}/{repo}/-/archive/HEAD/{repo}-HEAD.tar.gz"

    try:
        body = await fetch(ref_url)
    except Exception as exc:  # noqa: BLE001 - an unreachable repo is a finding about the paper
        accessible, detail = _accessibility_for(exc)
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            reason=f"{url} could not be reached ({detail})",
            accessibility={url: {"accessible": accessible, "reason": detail}},
        )

    try:
        sha = str((json.loads(body.decode()) or {}).get("sha") or "").strip()
    except Exception:  # noqa: BLE001
        sha = ""
    if not sha:
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            reason=f"{url} could not be pinned to a commit, so there is no way to say which code ran",
            accessibility={url: {"accessible": "no", "reason": "no commit sha was returned"}},
        )

    try:
        blob = await fetch(f"{tar_url}{sha}" if kind == "github" else tar_url)
    except Exception as exc:  # noqa: BLE001
        accessible, detail = _accessibility_for(exc)
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            commit_sha=sha,
            reason=f"{url} resolved to {sha} but its contents could not be downloaded ({detail})",
            accessibility={url: {"accessible": accessible, "reason": detail}},
        )

    files, problem = _unpack(blob, max_bytes=max_bytes, max_files=max_files)
    if problem:
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            commit_sha=sha,
            reason=problem,
            accessibility={url: {"accessible": "no", "reason": problem}},
        )
    return CodeResolution(
        RESOLVED,
        kind=kind,
        url=url,
        commit_sha=sha,
        files=files,
        archive=blob,
        reason=f"{url} pinned at {sha}",
        accessibility={url: {"accessible": "yes", "reason": None}},
    )


async def _fetch_artifact(source: dict, fetch: Fetcher, *, max_bytes: int, max_files: int) -> CodeResolution:
    """Fetch a code ARTIFACT: a supplementary archive, a journal-hosted download, a script.

    When it sits in the GEO deposit it is already listed by ``list_deposit``, so no new fetcher is
    needed; when it is journal-hosted and unreachable that is ``code_unreachable``, which is a true
    statement about the paper.
    """
    kind, url = source["kind"], source.get("url") or ""
    if not url:
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=None,
            reason=f"the paper names {source.get('identifier')} but no location to fetch it from",
        )
    try:
        blob = await fetch(url)
    except Exception as exc:  # noqa: BLE001
        accessible, detail = _accessibility_for(exc)
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            reason=f"{url} could not be downloaded ({detail})",
            accessibility={url: {"accessible": accessible, "reason": detail}},
        )

    # A bare script is not an archive; it is one file and it is usable as it is.
    if not (blob[:2] == b"PK" or blob[:2] == b"\x1f\x8b" or blob[:5] == b"ustar"):
        name = url.rsplit("/", 1)[-1] or "analysis"
        if len(blob) > max_bytes:
            problem = f"{name} is larger than the {max_bytes}-byte cap for fetched code"
            return CodeResolution(
                CODE_UNREACHABLE,
                kind=kind,
                url=url,
                reason=problem,
                accessibility={url: {"accessible": "no", "reason": problem}},
            )
        return CodeResolution(
            RESOLVED,
            kind=kind,
            url=url,
            files=[{"path": name, "size_bytes": len(blob)}],
            archive=blob,
            reason=f"{url} fetched as a single file",
            accessibility={url: {"accessible": "yes", "reason": None}},
        )

    files, problem = _unpack(blob, max_bytes=max_bytes, max_files=max_files)
    if problem:
        return CodeResolution(
            CODE_UNREACHABLE,
            kind=kind,
            url=url,
            reason=problem,
            accessibility={url: {"accessible": "no", "reason": problem}},
        )
    return CodeResolution(
        RESOLVED,
        kind=kind,
        url=url,
        files=files,
        archive=blob,
        reason=f"{url} fetched and unpacked",
        accessibility={url: {"accessible": "yes", "reason": None}},
    )


def _ordered(sources: list[dict], deposit_entries: list) -> list[dict]:
    """The owner's order: the code artifact first, then the repository, then anything else.

    A code archive sitting in the GEO deposit is a source even when the extractor did not name it,
    because step 1 already listed it and step 16's new ``code`` classification is what makes it
    findable.
    """
    named = [s for s in sources or [] if isinstance(s, dict)]
    from_deposit = [
        {"kind": "supplementary", "url": e.url, "identifier": e.filename}
        for e in deposit_entries or []
        if getattr(e, "classification", None) == "code" and not any((s.get("url") or "") == e.url for s in named)
    ]
    artifacts = [s for s in named if s.get("kind") not in _REPO_KINDS]
    repos = [s for s in named if s.get("kind") in _REPO_KINDS]
    return [*from_deposit, *artifacts, *repos]


async def resolve_code(
    *,
    sources: list[dict],
    deposit_entries: list | None = None,
    fetcher: Fetcher,
    max_bytes: int = _MAX_BYTES,
    max_files: int = _MAX_FILES,
) -> CodeResolution:
    """Fetch and pin the authors' code, trying every recorded source. Never raises.

    Returns ``code_absent`` only when the paper named nothing at all. Everything the paper DID name
    is attempted, and the failures of the ones that did not resolve are kept on ``attempts`` rather
    than discarded: a reader disputing the verdict needs to see what was tried.
    """
    ordered = _ordered(sources, deposit_entries or [])
    if not ordered:
        return CodeResolution(
            CODE_ABSENT,
            reason="This paper names no analysis code: no repository, no archive and no script.",
        )

    attempts: list[dict] = []
    accessibility: dict[str, dict] = {}
    last_failure: CodeResolution | None = None

    for source in ordered:
        kind = source.get("kind") or "other"
        if kind in _REPO_KINDS:
            result = await _fetch_repository(source, fetcher, max_bytes=max_bytes, max_files=max_files)
        else:
            result = await _fetch_artifact(source, fetcher, max_bytes=max_bytes, max_files=max_files)

        accessibility.update(result.accessibility)
        attempts.append(
            {
                "kind": kind,
                "url": source.get("url"),
                "ok": result.outcome == RESOLVED,
                "reason": result.reason,
                "commit_sha": result.commit_sha,
            }
        )
        if result.outcome == RESOLVED:
            result.attempts = attempts
            result.accessibility = accessibility
            return result
        last_failure = result

    return CodeResolution(
        CODE_UNREACHABLE,
        kind=last_failure.kind if last_failure else None,
        url=last_failure.url if last_failure else None,
        reason=(
            "Every code source this paper names was attempted and none could be fetched: "
            + "; ".join(a["reason"] for a in attempts if a["reason"])
        ),
        attempts=attempts,
        accessibility=accessibility,
    )
