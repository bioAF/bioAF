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

from app.services.result_set_normalizer import normalize_gene_table, normalize_interval_table

logger = logging.getLogger("bioaf.ground_truth_fetch")

_GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo/series"
_TIMEOUT = httpx.Timeout(30.0)

Fetcher = Callable[[str], Awaitable[str]]

# Filename token sets (promoted from spike-03's scan_geo_suppl classifier).
_DE_TOKENS = ("deg", "_de_", "_de.", "diffexp", "differential_expression", "deseq", "edger", "dge", "limma")
_DA_TOKENS = ("diffbind", "diff_peak", "differential_peak", "da_peak", "_da.", "_da_")
_COUNT_TOKENS = ("count", "tpm", "fpkm", "rpkm", "matrix")


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
    raw / other. Best-effort, from the filename alone (spike-03)."""
    n = (name or "").lower()
    if n.endswith("_raw.tar") or n == "filelist.txt":
        return "raw"
    if any(t in n for t in _DA_TOKENS) or ("peak" in n and any(t in n for t in ("diff", "_da", "gain", "lost"))):
        return "da_table"
    if any(t in n for t in _DE_TOKENS):
        return "de_table"
    if any(t in n for t in _COUNT_TOKENS):
        return "counts"
    return "other"


def parse_dir_listing(html: str) -> list[str]:
    """Extract supplementary filenames from an NCBI FTP-over-HTTP directory listing (href links),
    excluding parent/self and absolute links."""
    names: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html or ""):
        if href in ("../", "./", "/") or href.startswith(("http://", "https://", "?", "#")):
            continue
        name = href.rstrip("/").split("/")[-1]
        if name and name not in names:
            names.append(name)
    return names


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
