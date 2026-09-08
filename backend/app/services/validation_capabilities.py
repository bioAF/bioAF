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

from app.services.deposit_selection import selectable
from app.services.literature.accession_manifest_service import (
    AccessionManifestService,
    ManifestResult,
)
from app.services.literature.deposit_inventory_service import list_deposit

logger = logging.getLogger("bioaf.validation_capabilities")

Fetcher = Callable[[str], Awaitable[str]]

YES = "yes"
NO = "no"
UNKNOWN = "unknown"

# Existence is settled here. Accessibility waits for something to actually fetch the thing.
NOT_ATTEMPTED = "not_attempted"

# A code source that lives in a repository rather than as a downloadable artifact. The split matters
# because the checklist's `Code artifact exists` row takes NO / GitHub / Download / UNKNOWN, and
# step 16 resolves the artifact FIRST and the repository second.
_REPO_KINDS = ("github", "gitlab")


def _answer(value: str, *, evidence: str | None = None, failure_reason: str | None = None) -> dict:
    return {"value": value, "evidence": evidence, "failure_reason": failure_reason}


def _raw_data_answer(rows: list[dict]) -> dict:
    """Whether there are raw reads something could actually fetch.

    **Registered is not the same as available**, measured live on GSE96583 (2026-09-07): ENA returns
    run rows and a read count with ZERO ``fastq_bytes``, because the study deposits 10x BAMs rather
    than FASTQ. Answering YES off the run rows alone would put a route on the checklist that nothing
    can run.
    """
    if not rows:
        return _answer(NO, evidence="ENA lists no sequencing runs for this study")
    with_fastq = [r for r in rows if (r.get("fastq_bytes") or "").strip()]
    if not with_fastq:
        return _answer(
            NO,
            evidence=(
                f"ENA lists {len(rows)} run(s) for this study, none of which publishes FASTQ files "
                "(a study can register its runs and deposit aligned reads instead)"
            ),
        )
    return _answer(YES, evidence=f"ENA publishes FASTQ files for {len(with_fastq)} of {len(rows)} run(s)")


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


async def _ena_rows(accession: str, fetch: Fetcher, manifest: ManifestResult) -> tuple[list[dict] | None, str | None]:
    """The ENA run rows for this study, or a reason we could not get them.

    Reuses ``accession_manifest_service``'s ENA client rather than adding a second one. The study's
    SRA accession comes from the series matrix the manifest already parsed, so a GEO failure and an
    ENA failure stay distinguishable.
    """
    from app.services.literature.accession_manifest_service import (
        _ena_filereport_url,
        parse_ena_filereport,
    )

    target = accession
    if manifest.samples:
        # A GEO series is not itself queryable at ENA; its SRA study is. The manifest resolved that
        # link already, so re-deriving it here would be a second network round trip for an answer we
        # are holding.
        exp = next((s.get("experiment_accession") for s in manifest.samples if s.get("experiment_accession")), None)
        target = exp or accession
    try:
        tsv = await fetch(_ena_filereport_url(target))
    except Exception as exc:  # noqa: BLE001 - a discovery failure is UNKNOWN, never an absence
        logger.info("ENA availability check failed for %s: %s", target, exc)
        return None, "bioAF could not reach ENA to check whether raw reads are published"
    return parse_ena_filereport(tsv), None


async def discover_capabilities(
    *,
    accession: str | None,
    has_full_text: bool,
    code_availability: list[dict] | None,
    fetcher: Fetcher | None = None,
) -> dict:
    """Answer all seven phase-1 questions for one paper. Never raises.

    Returns the ``evidence["capabilities"]`` bundle: one tri-state answer per question, plus the
    per-source code list carrying existence and accessibility separately.
    """
    acc = (accession or "").strip()

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

    if not acc:
        # Nothing to look up. An established absence, not a failed lookup: reporting it as UNKNOWN
        # would suggest a retry might find something.
        absent = _answer(NO, evidence="this study names no deposited accession")
        for key in ("geo_entry", "raw_data", "preprocessed_data", "sample_metadata"):
            caps[key] = dict(absent)
        return caps

    # The series matrix answers TWO rows: the GEO record exists (it parsed), and the entry carries
    # per-sample metadata (it listed samples). One fetch, and the manifest service already caches
    # the multi-platform fallback that a combined-matrix 404 needs.
    try:
        manifest = await AccessionManifestService.fetch_manifest(acc, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001 - the service does not raise, but discovery must not either
        logger.info("series matrix discovery failed for %s: %s", acc, exc)
        manifest = ManifestResult(unavailable_reason="bioAF could not reach GEO to read this study's record")

    if manifest.samples:
        caps["geo_entry"] = _answer(YES, evidence=f"GEO published a series record for {acc}")
        caps["sample_metadata"] = _answer(
            YES, evidence=f"the series record describes {len(manifest.samples)} sample(s)"
        )
    else:
        reason = manifest.unavailable_reason or f"GEO returned no series record for {acc}"
        caps["geo_entry"] = _answer(UNKNOWN, failure_reason=reason)
        caps["sample_metadata"] = _answer(UNKNOWN, failure_reason=reason)

    rows, ena_failure = await _ena_rows(acc, fetcher or _default_fetch, manifest)
    caps["raw_data"] = _answer(UNKNOWN, failure_reason=ena_failure) if rows is None else _raw_data_answer(rows)

    inventory = await list_deposit(acc, fetcher=fetcher)
    if inventory.unavailable_reason:
        # An unlistable directory is a discovery failure. It says nothing about whether the record
        # exists, which is why that row was answered above from the series matrix instead.
        caps["preprocessed_data"] = _answer(UNKNOWN, failure_reason=inventory.unavailable_reason)
    else:
        usable = selectable(inventory.entries)
        caps["preprocessed_data"] = _answer(
            YES if usable else NO,
            evidence=(
                f"{len(usable)} of {len(inventory.entries)} deposited file(s) could serve as a reproduction input"
                if usable
                else f"GEO lists {len(inventory.entries)} supplementary file(s), none holding per-feature values"
            ),
        )
    return caps


async def _default_fetch(url: str) -> str:
    from app.services.literature.accession_manifest_service import _http_fetch_text

    return await _http_fetch_text(url)
