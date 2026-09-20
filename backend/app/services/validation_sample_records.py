"""plan_8_5 section 3.4: the deposit's own sample records, retrieved for whatever the study resolved.

The lookup bioAF had was built from ``study.source_accession``, the accession someone optionally
typed into the request. A paper submitted by DOI has none, so no URL was built, nothing was fetched,
and the species check reported "the deposit declares no organism to compare against" about a series
it had never opened. That sentence is about the deposit; the fact was about bioAF.

So two rules hold here:

- **the accessions are the ones the study resolved**, wherever they came from: the request, the
  reading of the paper, or the text scan. A deposit that reached the capability checklist is a
  deposit this stage can open;
- **a failure is bioAF's, and says which failure it is.** No adapter for the archive, an unreachable
  matrix and a matrix that was read and states nothing are three different records, and only the
  last one says anything about the deposit.

Retrieval only. Nothing here judges a paper: the records are evidence, and
``validation_rubric_evidence`` decides what they establish.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

logger = logging.getLogger("bioaf.validation_sample_records")

# What bioAF can read per-sample records from today. An archive absent from this is a limitation of
# bioAF's, declared as one; it is never evidence about the deposit.
SUPPORTED_ARCHIVES = ("geo",)
_MAX_DEPOSITS = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _archive_of(deposit: dict) -> str:
    from app.services.archive_discovery import classify_archive

    archive = str(deposit.get("archive") or "").strip().lower()
    return archive or str(classify_archive(str(deposit.get("accession") or "")) or "").strip().lower()


async def collect_sample_records(*, deposits: list[dict] | None, fetcher=None, limit: int = _MAX_DEPOSITS) -> dict:
    """``{"deposits": [...], "limitations": [...]}`` for every deposit this study resolved.

    One entry per deposit bioAF could open, carrying its samples, the URL they were read from, when,
    and the checksum of the bytes. One limitation per deposit it could not, carrying why.
    """
    from app.services.literature.accession_manifest_service import (
        _http_fetch_text,
        geo_series_matrix_url,
        parse_sample_records,
    )

    fetch = fetcher or _http_fetch_text
    held: list[dict] = []
    limitations: list[dict] = []
    seen: set[str] = set()
    for deposit in deposits or []:
        if not isinstance(deposit, dict):
            continue
        accession = str(deposit.get("accession") or "").strip()
        if not accession or accession.upper() in seen:
            continue
        seen.add(accession.upper())
        archive = _archive_of(deposit)
        url = geo_series_matrix_url(accession) if archive in SUPPORTED_ARCHIVES else None
        if url is None:
            limitations.append(
                {
                    "accession": accession,
                    "archive": archive or None,
                    "reason": (
                        f"bioAF reads per-sample records from {', '.join(a.upper() for a in SUPPORTED_ARCHIVES)} "
                        f"only, and {accession} is in {archive.upper() if archive else 'an archive bioAF did not recognise'}"
                    ),
                }
            )
            continue
        if len(held) >= limit:
            limitations.append(
                {
                    "accession": accession,
                    "archive": archive,
                    "reason": f"bioAF opened {limit} deposits for this study and stopped there",
                }
            )
            continue
        try:
            text = await fetch(url)
        except Exception as exc:  # noqa: BLE001 - an unreachable deposit is a limitation, never a verdict
            logger.info("sample records for %s could not be read: %s", accession, exc)
            limitations.append(
                {
                    "accession": accession,
                    "archive": archive,
                    "source": url,
                    "reason": f"{accession}'s sample records could not be read from {url}",
                }
            )
            continue
        samples = parse_sample_records(text)
        held.append(
            {
                "accession": accession,
                "archive": archive,
                "provenance": deposit.get("provenance"),
                "scoped": bool(deposit.get("scoped")),
                "source": url,
                "at": _now_iso(),
                "sha256": hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest(),
                "sample_count": len(samples),
                "samples": samples,
            }
        )
    return {"deposits": held, "limitations": limitations}
