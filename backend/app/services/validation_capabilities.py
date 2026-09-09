"""plan_7 step 13: what a paper actually has, established in one pass at read time.

DESIRED STATE phase 1. For ONE paper, answer each of: can the paper be read, does a GEO entry
exist, is raw sample data available, is pre-processed data available, does the GEO entry include
sample metadata, does code exist as an attachment, is code linked on GitHub.

**None of these rules a paper in or out.** They determine the best available path and how high a
validation level is reachable. That is why this runs at READ time, before the C1 gate: the route
modal that shipped in ``7d36acad`` offers all three routes blind, so a person can choose the GEO
route on a paper with no deposited matrix and discover it only after approving.

**Every answer is YES, NO or UNKNOWN.** A boolean has no room for "discovery could not establish
either", so a timeout, a 502 or an unparseable listing would render as an established absence.
"We could not find out" and "it is not there" are different statements about a paper.

**Existence and accessibility are separate answers.** A repository can be known to exist and be
private; a supplementary archive can be listed and 404 on download. Existence is answered here;
accessibility is answered when something tries to fetch it (step 16), and the checklist carries
both.

**GEO existence is established independently of the supplementary listing.** A failed listing of
``.../suppl/`` says nothing about whether the GEO record exists. Existence comes from the series
matrix, which this already fetches for the sample-metadata row, so it costs no extra call: an
ordering choice, not new work.

**One check failing does not abandon the others.** Each question is answered independently and
carries its own failure reason, so a GEO outage cannot blank the code rows.

Cheap: two HTTP calls beyond what reading a paper already does, no model, no compute. It runs on
every paper read, including the ones nobody approves.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.services.archive_discovery import NO, UNKNOWN, YES, describe_deposit, is_sample_accession

logger = logging.getLogger("bioaf.validation_capabilities")

Fetcher = Callable[[str], Awaitable[str]]

# Re-exported: the tri-state is answered per deposit now, and every existing caller imports it from
# here.
__all__ = ["NO", "NOT_ATTEMPTED", "UNKNOWN", "YES", "discover_capabilities"]

# Existence is settled at read time. Accessibility waits for something to actually fetch the thing.
NOT_ATTEMPTED = "not_attempted"

# A paper naming a dozen deposits must not turn one read into two dozen HTTP calls. Three covers
# every real paper seen so far; the rest are carried unnamed rather than described.
_MAX_DEPOSITS = 3

# A code source that lives in a repository rather than as a downloadable artifact. The split matters
# because the checklist's `Code artifact exists` row takes NO / GitHub / Download / UNKNOWN, and
# step 16 resolves the artifact FIRST and the repository second.
_REPO_KINDS = ("github", "gitlab")


def _answer(value: str, *, evidence: str | None = None, failure_reason: str | None = None) -> dict:
    return {"value": value, "evidence": evidence, "failure_reason": failure_reason}


def _code_answers(code_availability: list[dict] | None) -> tuple[list[dict], dict, dict]:
    """The paper's code sources, and the two checklist rows derived from them.

    ``None`` and ``[]`` are different facts: ``None`` is "the extraction never answered this", which
    is UNKNOWN, and ``[]`` is "we read the paper and it named no code", which is NO.
    """
    if code_availability is None:
        unknown = _answer(UNKNOWN, failure_reason="the paper's code availability was never extracted")
        return [], unknown, dict(unknown)

    sources = [
        {
            "kind": row.get("kind") or "other",
            "url": row.get("url"),
            "identifier": row.get("identifier"),
            "exists": YES,
            # Answered by step 16, when something tries to fetch it. A repository can be known and
            # private, and flattening the two facts into one cell loses the useful half.
            "accessible": NOT_ATTEMPTED,
            "accessible_reason": None,
        }
        for row in code_availability
        if isinstance(row, dict)
    ]

    repos = [s for s in sources if s["kind"] in _REPO_KINDS]
    artifacts = [s for s in sources if s["kind"] not in _REPO_KINDS]

    def _where(rows: list[dict]) -> str | None:
        """Where each source is, for the checklist's detail cell. A row with neither a URL nor an
        identifier still names its kind, because "Download" is more use than an empty cell."""
        return ", ".join(str(s.get("url") or s.get("identifier") or s.get("kind")) for s in rows) or None

    return (
        sources,
        _answer(YES if artifacts else NO, evidence=_where(artifacts)),
        _answer(YES if repos else NO, evidence=_where(repos)),
    )


async def discover_capabilities(
    *,
    accessions: list[dict],
    has_full_text: bool,
    code_availability: list[dict] | None,
    fetcher: Fetcher | None = None,
) -> dict:
    """Answer the phase-1 questions for one paper, across every deposit it names. Never raises.

    Returns the ``evidence["capabilities"]`` bundle: the per-deposit answers, the paper-level rows
    aggregated over them, and the per-source code list carrying existence and accessibility
    separately.

    ``accessions`` carries provenance, because a requested accession and one extracted from the
    paper are different claims. change_7.1 section 1: a study requested by DOI has no
    ``source_accession`` at all, and passing that empty string into GEO's implementation answered NO
    to every question for a paper that deposited 54 samples and 108 FASTQ files.
    """
    caps: dict = {
        "paper_readable": _answer(
            YES if has_full_text else NO,
            evidence=(
                "bioAF holds the paper's full text" if has_full_text else "bioAF has no full text for this paper"
            ),
        )
    }

    sources, artifact_answer, repo_answer = _code_answers(code_availability)
    caps["code_sources"] = sources
    caps["code_artifact"] = artifact_answer
    caps["code_repository"] = repo_answer

    deposits = await _describe_deposits(accessions, fetcher or _default_fetch)
    caps["deposits"] = deposits

    if not deposits:
        # Nothing to look up. An established absence, not a failed lookup: reporting it as UNKNOWN
        # would suggest a retry might find something.
        absent = _answer(NO, evidence="this paper names no deposited accession")
        for key in ("deposit_exists", "raw_data", "preprocessed_data", "sample_metadata"):
            caps[key] = dict(absent)
        return caps

    caps["deposit_exists"] = _aggregate(deposits, "exists")
    for key in ("raw_data", "preprocessed_data", "sample_metadata"):
        caps[key] = _aggregate(deposits, key)
    return caps


async def _describe_deposits(accessions: list[dict], fetcher: Fetcher) -> list[dict]:
    """Every named deposit, deduplicated, described in provenance order and bounded in number.

    The requested accession is described first and marked scoped, because it is the one a run would
    use. The rest are described because the paper named them: an accession the requester did not
    scope is still evidence about what the authors deposited.
    """
    seen: set[str] = set()
    wanted: list[dict] = []
    for entry in accessions or []:
        acc = str((entry or {}).get("accession") or "").strip()
        if not acc or acc.upper() in seen:
            continue
        # A sample is not a deposit. An extraction naming GSM1 and GSM2 is naming two samples of a
        # series, and describing each as its own deposit would put "Deposit (GEO) GSM1" on the
        # checklist and spend a round of lookups per sample on every paper read.
        if is_sample_accession(acc):
            continue
        seen.add(acc.upper())
        wanted.append({"accession": acc, "provenance": (entry or {}).get("provenance") or "extracted"})

    requested = [e for e in wanted if e["provenance"] == "requested"]
    ordered = requested + [e for e in wanted if e["provenance"] != "requested"]
    # The scoped deposit is the requested one, or the only one when nothing was scoped. A paper
    # naming three deposits and no request has no scope until someone picks one.
    scoped_accession = (
        requested[0]["accession"] if requested else (ordered[0]["accession"] if len(ordered) == 1 else None)
    )

    described: list[dict] = []
    for entry in ordered[:_MAX_DEPOSITS]:
        described.append(
            await describe_deposit(
                entry["accession"],
                provenance=entry["provenance"],
                scoped=entry["accession"] == scoped_accession,
                fetcher=fetcher,
            )
        )
    return described


def _aggregate(deposits: list[dict], key: str) -> dict:
    """One paper-level answer over every deposit.

    **YES beats NO beats UNKNOWN.** One deposit holding reads means the paper published reads, and a
    single unreadable listing must not erase a positive answer from its sibling. The evidence names
    which deposit answered, so an aggregate is never a claim without a source.
    """
    answers = [(d.get("accession"), d.get(key), d.get("evidence"), d.get("failure_reason")) for d in deposits]
    for wanted in (YES, NO):
        matching = [a for a in answers if a[1] == wanted]
        if matching:
            return _answer(
                wanted,
                evidence="; ".join(f"{acc}: {ev}" for acc, _, ev, _ in matching if ev) or None,
            )
    return _answer(
        UNKNOWN,
        failure_reason="; ".join(reason for _, _, _, reason in answers if reason) or None,
    )


async def _default_fetch(url: str) -> str:
    from app.services.literature.accession_manifest_service import _http_fetch_text

    return await _http_fetch_text(url)
