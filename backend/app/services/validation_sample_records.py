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
    from app.services.literature.accession_manifest_service import _http_fetch_text, geo_series_matrix_url

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
        samples, sources, digest, reason = await _matrices(accession, url, fetch)
        if reason is not None:
            limitations.append({"accession": accession, "archive": archive, "source": url, "reason": reason})
            continue
        held.append(
            {
                "accession": accession,
                "archive": archive,
                "provenance": deposit.get("provenance"),
                "scoped": bool(deposit.get("scoped")),
                "source": sources[0],
                "sources": sources,
                "at": _now_iso(),
                "sha256": digest,
                "sample_count": len(samples),
                "samples": samples,
            }
        )
    return {"deposits": held, "limitations": limitations}


async def _matrices(accession: str, url: str, fetch) -> tuple[list[dict], list[str], str, str | None]:
    """Every sample record of one GEO series: ``(samples, sources, sha256, reason)``.

    plan_8_5 section 3.4, from the live run on study 64. GEO publishes the combined
    ``GSE<n>_series_matrix.txt.gz`` only for a series that used ONE instrument. GSE144396 spans two
    (SAMD1's ChIP-seq and its RNA-seq), so the single URL bioAF built is a 404 and the whole deposit
    read as unreachable. The folder is what says which matrices exist, which is the same fallback
    `AccessionManifestService` has always used for the sample manifest.
    """
    from app.services.literature.accession_manifest_service import (
        geo_matrix_dir_url,
        parse_matrix_directory,
        parse_sample_records,
    )

    texts: list[tuple[str, str]] = []
    try:
        texts.append((url, await fetch(url)))
    except Exception as exc:  # noqa: BLE001 - one missing combined matrix is not an unreachable deposit
        logger.info("no combined series matrix for %s (%s); listing the folder", accession, exc)
        folder = geo_matrix_dir_url(accession)
        try:
            listing = await fetch(folder) if folder else ""
        except Exception as listing_exc:  # noqa: BLE001 - now the deposit really is unreachable
            logger.info("sample records for %s could not be read: %s", accession, listing_exc)
            return [], [], "", f"{accession}'s sample records could not be read from {url}"
        names = parse_matrix_directory(listing)
        if not names:
            return [], [], "", f"GEO has published no series matrix for {accession}"
        for name in names:
            try:
                texts.append((f"{folder}{name}", await fetch(f"{folder}{name}")))
            except Exception as one:  # noqa: BLE001 - a platform bioAF could not read is said so
                logger.info("one matrix of %s could not be read: %s", accession, one)
    if not texts:
        return [], [], "", f"{accession}'s sample records could not be read from {url}"
    samples: list[dict] = []
    seen: set[str] = set()
    for _source, text in texts:
        for sample in parse_sample_records(text):
            key = str(sample.get("accession") or "") or f"{len(samples)}"
            if key in seen:
                continue
            seen.add(key)
            samples.append(sample)
    digest = hashlib.sha256("".join(text for _s, text in texts).encode("utf-8", errors="replace")).hexdigest()
    return samples, [source for source, _t in texts], digest, None
