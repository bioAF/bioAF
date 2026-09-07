"""B4 auto-fetch ASSIST (lit_validation Level-3, ADR-069 / spec-08).

Best-effort acquisition of a paper's deposited differential result set (its DEG table / DA peak list)
to PRE-FILL the human confirm at the C1 gate. It is never unattended ground truth: spike-03 found the
result is in GEO supplementary only ~3.8% of the time for DE and ~never for DA, so the differential
result is mostly journal-SI-bound, and journal SI is publisher-specific + often gated. This module
therefore covers the one deterministic route (GEO supplementary listing + classify + parse) and
returns best-effort candidates; when it finds nothing (the common case), the human-supply path in
`ReproductionPlanService.set_finding_claim` is the backbone.

The HTTP boundary is a small injectable async callable so the orchestration is unit-testable without
network; the default fetcher uses httpx and transparently gunzips `.gz` supplementary files.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

from app.services.literature.deposit_inventory_service import classify_deposit_filename, parse_dir_listing
from app.services.result_set_normalizer import normalize_gene_table, normalize_interval_table

logger = logging.getLogger("bioaf.ground_truth_fetch")

_GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo/series"
_TIMEOUT = httpx.Timeout(30.0)

Fetcher = Callable[[str], Awaitable[str]]

# This module's five buckets, expressed over the deposit route's finer vocabulary (plan_7 step 1).
# Anything the deposit route separates but this caller does not care about collapses to `other`.
#
# There is ONE classifier now. The token sets that used to live here were built from spike-03's scan
# and could only recognise what somebody had enumerated, which is how GSE273743's csaw table and
# GSE213770's DMR table both came back as "other" while being exactly the ground truth the C1 gate
# was asking a human to go and find. Keeping a second list here is how the two drift apart again.
_DEPOSIT_TO_ASSIST = {
    "matrix_counts": "counts",
    "matrix_normalized": "counts",
    "de_table": "de_table",
    "da_table": "da_table",
    "raw": "raw",
}


def geo_suppl_dir_url(accession: str) -> str | None:
    """Build the GEO supplementary directory URL for a GSE accession, or None if not a GSE id.

    GEO groups series in `GSE<prefix>nnn/` where the last three digits are masked (GSE309060 ->
    GSE309nnn; GSE12 -> GSEnnn).
    """
    m = re.fullmatch(r"GSE(\d+)", (accession or "").strip().upper())
    if not m:
        return None
    num = m.group(1)
    stub = f"GSE{num[:-3]}nnn" if len(num) > 3 else "GSEnnn"
    return f"{_GEO_FTP}/{stub}/GSE{num}/suppl/"


def classify_supplementary_filename(name: str) -> str:
    """Classify a GEO supplementary filename by what it likely holds: de_table / da_table / counts /
    raw / other. Best-effort, from the filename alone.

    Delegates to the deposit route's classifier and maps its finer answer back to these five, so both
    routes recognise a deposit identically and this caller's contract is unchanged.
    """
    return _DEPOSIT_TO_ASSIST.get(classify_deposit_filename(name), "other")


# Re-exported from the deposit route so ONE parser reads a GEO listing. The two copies were
# identical except that this one did not exclude `/`-prefixed hrefs, and NCBI writes the parent link
# as an absolute path: on the real GSE273743 listing that returned the series accession itself,
# `GSE273743`, as a supplementary filename. Latent rather than live (the phantom classified as
# `other` and was filtered before any fetch) but one token away from a download aimed at a directory.
#
# Same consolidation `classify_supplementary_filename` got: keeping a second copy is how the two
# drifted apart in the first place.


async def _http_fetch_text(url: str) -> str:
    """Default fetcher: GET the URL and return text, transparently gunzipping `.gz` payloads."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        if url.lower().endswith(".gz"):
            return gzip.decompress(r.content).decode("utf-8", errors="replace")
        return r.text


class GroundTruthFetchService:
    @staticmethod
    async def fetch_geo_candidates(accession: str, *, kind: str = "gene", fetcher: Fetcher | None = None) -> list[dict]:
        """List a GEO series' supplementary dir, download the DE (gene) or DA (interval) table
        candidates, and parse each into a best-effort FindingSet. Returns [] on any failure or when
        nothing matches (assist, never a gate)."""
        url = geo_suppl_dir_url(accession)
        if not url:
            return []
        fetch = fetcher or _http_fetch_text
        try:
            listing = await fetch(url)
        except Exception:
            logger.info("GEO suppl listing fetch failed for %s (assist; falling back to human supply)", accession)
            return []

        wanted = "da_table" if kind == "interval" else "de_table"
        candidates: list[dict] = []
        for name in parse_dir_listing(listing):
            if classify_supplementary_filename(name) != wanted:
                continue
            file_url = url + name
            try:
                text = await fetch(file_url)
            except Exception:
                logger.info("GEO suppl file fetch failed: %s", file_url)
                continue
            fs = normalize_interval_table(text) if kind == "interval" else normalize_gene_table(text)
            candidates.append(
                {
                    "source": "geo_supplementary",
                    "filename": name,
                    "url": file_url,
                    "n_sig": len(fs.entities),
                    "finding_set": fs.to_dict(),
                    # The raw table so the C1 gate can pre-fill the confirm textarea; the human reviews
                    # and confirms through the same normalize-on-submit path (never auto-confirmed).
                    "table_text": text,
                }
            )
        return candidates
