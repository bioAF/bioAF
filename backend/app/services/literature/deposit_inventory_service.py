"""plan_7 step 1: what a GEO study actually deposited.

The pipeline route reproduces a paper by pulling its raw reads and re-running the whole analysis.
The deposit route starts from the pre-processed data the authors published instead, which is faster
and, per the bioinformaticians who asked for it, is where the disagreements actually live. This
module is that route's first step: resolve an accession into an honest inventory of its
supplementary files, at the series level AND per sample, each classified by what it holds.

**Two listing sources, and the cheap one is preferred.** When a series has a ``_RAW.tar``, GEO also
publishes ``filelist.txt``: a tab-separated manifest naming every member file with its size and a
controlled ``Type``. That is one request for the whole deposit, including the per-sample files, so it
is tried first. A series with no ``_RAW.tar`` has no filelist (verified on GSE273743 and GSE213770,
2026-09-05), and falls back to parsing the supplementary directory listing.

Preferring the filelist is not only about politeness to NCBI. GSE157174 has twelve samples, and
walking per-sample directories would be twelve requests to learn what one already says.

**The depositor's ``Type`` outranks our filename tokens.** It is the depositor's own controlled
statement of what the file is, which is the same reason ``library_strategy`` outranks the paper's
prose when the two disagree (see ``ReproductionPlan.library_strategy``).

Mirrors ``ground_truth_fetch_service`` and ``accession_manifest_service``: the HTTP boundary is a
small injectable async callable so the orchestration is unit-testable without network, and nothing
here raises. A failure yields an empty inventory carrying a human-readable reason, because a deposit
we cannot list is a reason to take the pipeline route, never an error on the study.
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("bioaf.deposit_inventory")

Fetcher = Callable[[str], Awaitable[str]]

_GEO_SERIES = "https://ftp.ncbi.nlm.nih.gov/geo/series"
_GEO_SAMPLES = "https://ftp.ncbi.nlm.nih.gov/geo/samples"
_TIMEOUT = httpx.Timeout(30.0)


def _mask(accession: str, prefix: str) -> str | None:
    """GEO's directory masking: the last three digits become ``nnn`` (GSE309060 -> GSE309nnn,
    GSM4758351 -> GSM4758nnn), and an accession of three digits or fewer masks to the bare prefix.

    One function for both accession types. The GSE form of this rule is written out twice already
    (``ground_truth_fetch_service.geo_suppl_dir_url`` and
    ``accession_manifest_service.geo_series_matrix_url``); a third and fourth copy is how the two
    quietly drift apart.
    """
    m = re.fullmatch(rf"{prefix}(\d+)", (accession or "").strip().upper())
    if not m:
        return None
    num = m.group(1)
    return f"{prefix}{num[:-3]}nnn" if len(num) > 3 else f"{prefix}nnn"


def series_suppl_url(accession: str) -> str | None:
    """The supplementary directory URL for a GSE series, or None if not a GSE id."""
    stub = _mask(accession, "GSE")
    return f"{_GEO_SERIES}/{stub}/{accession.strip().upper()}/suppl/" if stub else None


def sample_suppl_url(accession: str) -> str | None:
    """The supplementary directory URL for a GSM sample, or None if not a GSM id."""
    stub = _mask(accession, "GSM")
    return f"{_GEO_SAMPLES}/{stub}/{accession.strip().upper()}/suppl/" if stub else None


def filelist_url(accession: str) -> str | None:
    """The ``filelist.txt`` manifest URL for a GSE series, or None if not a GSE id.

    Present only when the series has a ``_RAW.tar``. Absent is normal, not an error.
    """
    base = series_suppl_url(accession)
    return f"{base}filelist.txt" if base else None


# ---- classification ----

# Ordered most-specific first; the ORDER is load-bearing and each group says why it sits where it
# does. Tokens are matched against the lowercased filename.
#
# Result tables come first because their names collide with everything else: `deseq2.annot.xls.gz`
# carries "annot" and is a DE table, not sample annotation, and a differential peak file carries
# "peak" and is a result, not a per-sample peak call.
_DA_TOKENS = (
    "diffbind",
    "diff_peak",
    "differential_peak",
    "da_peak",
    "_da.",
    "_da_",
    # csaw and DiffBind both prefix their output columns AND name their files after themselves.
    # GSE273743's differential-binding table is `..._csaw.dba_window.set.csv.gz`, which matched none
    # of the tokens above, so the classifier called the paper's own ground truth "other". The column
    # resolver was fixed for this same family of deposits in a55fe889; this is the listing half of
    # the same defect.
    "csaw",
    "dba_",
    ".dba",
    # Differentially methylated regions/positions. GSE213770 deposits `GSE213770_DMR_DMB_...`, a
    # differential result with coordinates, which is an interval finding like any other. Anchored
    # with a separator so an unrelated word containing "dmr" cannot claim a file.
    "_dmr",
    "dmr_",
    "_dmp",
    "dmp_",
)
_DE_TOKENS = ("deg", "_de_", "_de.", "diffexp", "differential_expression", "deseq", "edger", "dge", "limma")
# Triplet parts before matrices: `GSM1_matrix.mtx.gz` is a triplet member and also carries "matrix".
_BARCODE_TOKENS = ("barcode",)
_FEATURE_TOKENS = ("feature", "genes.tsv")
_MTX_TOKENS = (".mtx",)
# Normalized before counts: `cpm_normalized_counts.tsv` is normalized, and "count" would claim it.
_NORMALIZED_TOKENS = ("tpm", "fpkm", "rpkm", "cpm", "normali", "_vst", "vst_", "rlog", "logcpm")
_COUNT_TOKENS = ("count", "matrix", "_umi", "umi_")
_PEAK_TOKENS = ("narrowpeak", "broadpeak", "gappedpeak", ".bed", "_peaks")
_COVERAGE_TOKENS = ("bigwig", ".bw", "bedgraph", ".wig")
_METADATA_TOKENS = ("metadata", "meta_", "_meta.", "pheno", "sample_info", "sampleinfo", "coldata", "annotation")

# GEO's controlled `Type` column in filelist.txt -> our bucket. The depositor stated it, so it wins
# over any filename guess. Only unambiguous types are mapped: `TSV` says nothing about content.
_TYPE_TO_CLASSIFICATION = {
    "NARROWPEAK": "peaks",
    "BROADPEAK": "peaks",
    "GAPPEDPEAK": "peaks",
    "BED": "peaks",
    "BIGWIG": "coverage",
    "BW": "coverage",
    "BEDGRAPH": "coverage",
    "WIG": "coverage",
    "MTX": "matrix_counts",
    "TAR": "raw",
    "BAM": "raw",
    "SRA": "raw",
    "FASTQ": "raw",
}


def classify_deposit_filename(name: str, deposited_type: str | None = None) -> str:
    """What a deposited file holds: one of ``raw``, ``da_table``, ``de_table``, ``barcodes``,
    ``features``, ``matrix_counts``, ``matrix_normalized``, ``peaks``, ``coverage``, ``metadata``,
    ``other``.

    ``deposited_type`` is GEO's own ``Type`` from ``filelist.txt`` and wins when it is one we
    recognise. An unrecognised type falls through to the filename, so a new GEO type degrades to
    today's behaviour rather than classifying everything as ``other``.
    """
    n = (name or "").lower()

    if n == "filelist.txt" or n.endswith("_raw.tar"):
        return "raw"

    mapped = _TYPE_TO_CLASSIFICATION.get((deposited_type or "").strip().upper())
    if mapped:
        return mapped

    if any(t in n for t in _DA_TOKENS) or ("peak" in n and any(t in n for t in ("diff", "gain", "lost"))):
        return "da_table"
    if any(t in n for t in _DE_TOKENS):
        return "de_table"
    if any(t in n for t in _BARCODE_TOKENS):
        return "barcodes"
    if any(t in n for t in _FEATURE_TOKENS):
        return "features"
    if any(t in n for t in _MTX_TOKENS):
        return "matrix_counts"
    if any(t in n for t in _NORMALIZED_TOKENS):
        return "matrix_normalized"
    if any(t in n for t in _COUNT_TOKENS):
        return "matrix_counts"
    if any(t in n for t in _PEAK_TOKENS):
        return "peaks"
    if any(t in n for t in _COVERAGE_TOKENS):
        return "coverage"
    if any(t in n for t in _METADATA_TOKENS):
        return "metadata"
    return "other"


_GSM_PREFIX_RE = re.compile(r"^(GSM\d+)", re.IGNORECASE)


def _gsm_of(filename: str) -> str | None:
    """The GSM this file belongs to, from its filename prefix. GEO names every per-sample
    supplementary file ``GSM<id>_...``; a series-level file has no such prefix."""
    m = _GSM_PREFIX_RE.match((filename or "").strip())
    return m.group(1).upper() if m else None


def parse_filelist(text: str) -> list[dict]:
    """Parse ``filelist.txt`` into one row per MEMBER file.

    Columns are ``#Archive/File  Name  Time  Size  Type``. The ``Archive`` row names the ``_RAW.tar``
    itself and is skipped: it is the container, not a candidate to reproduce from, and offering a
    50 MB tarball as a count matrix helps nobody. Never raises; junk yields [].
    """
    rows: list[dict] = []
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 5 or parts[0].strip().lower() != "file":
            continue
        filename = parts[1].strip()
        if not filename:
            continue
        try:
            size_bytes: int | None = int(parts[3].strip())
        except (ValueError, IndexError):
            size_bytes = None
        rows.append(
            {
                "filename": filename,
                "size_bytes": size_bytes,
                "deposited_type": parts[4].strip().upper() or None,
                "gsm": _gsm_of(filename),
            }
        )
    return rows


def parse_dir_listing(html: str) -> list[str]:
    """Filenames from an NCBI FTP-over-HTTP directory listing, excluding parent/self and absolute
    links. Same shape as ``ground_truth_fetch_service.parse_dir_listing``."""
    names: list[str] = []
    for href in re.findall(r'href="([^"]+)"', html or ""):
        if href in ("../", "./", "/") or href.startswith(("http://", "https://", "?", "#", "/")):
            continue
        name = href.rstrip("/").split("/")[-1]
        if name and name not in names:
            names.append(name)
    return names


@dataclass(frozen=True)
class DepositEntry:
    """One deposited supplementary file.

    ``size_bytes`` is None when it came from a directory listing, which states no sizes. None is
    honest; 0 would read as an empty file and would silently pass step 5's download cap.
    """

    filename: str
    url: str
    classification: str
    level: str  # "series" | "sample"
    gsm: str | None = None
    size_bytes: int | None = None
    deposited_type: str | None = None


@dataclass
class DepositInventory:
    """Everything a study deposited, or an explicit reason it could not be listed (never an
    exception). ``triplets`` are the 10x barcode/feature/matrix groups, already assembled."""

    entries: list[DepositEntry] = field(default_factory=list)
    triplets: list[dict] = field(default_factory=list)
    unavailable_reason: str | None = None
    source: str | None = None  # "filelist" | "directory"


def group_triplets(entries: list[DepositEntry]) -> list[dict]:
    """Assemble complete 10x triplets, one per GSM.

    A triplet is barcodes + features + matrix under one sample: three files that are ONE reproducible
    input. Deterministic on purpose, so the model is never asked to do a grouping that a filename
    prefix already answers.

    An incomplete group is not a triplet and its parts stay as loose entries. Two of three parts
    cannot be read as a matrix, and presenting them as one would fail at read time instead of here.
    """
    by_gsm: dict[str, dict[str, str]] = {}
    for e in entries:
        if not e.gsm or e.classification not in ("barcodes", "features", "matrix_counts"):
            continue
        role = {"barcodes": "barcodes", "features": "features", "matrix_counts": "matrix"}[e.classification]
        # `.mtx` is what makes a matrix_counts entry a triplet member; a plain counts TSV is a whole
        # matrix on its own and must not be pulled into a group.
        if role == "matrix" and ".mtx" not in e.filename.lower():
            continue
        by_gsm.setdefault(e.gsm, {})[role] = e.url
    return [
        {"gsm": gsm, "barcodes": parts["barcodes"], "features": parts["features"], "matrix": parts["matrix"]}
        for gsm, parts in sorted(by_gsm.items())
        if {"barcodes", "features", "matrix"} <= set(parts)
    ]


async def _http_fetch_text(url: str) -> str:
    """Default fetcher: GET and return text, transparently gunzipping `.gz`."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        if url.lower().endswith(".gz"):
            return gzip.decompress(r.content).decode("utf-8", errors="replace")
        return r.text


async def list_deposit(accession: str, *, fetcher: Fetcher | None = None) -> DepositInventory:
    """Inventory a GEO series' deposited supplementary files.

    Tries ``filelist.txt`` first (one request for the whole deposit, with sizes and types), then the
    supplementary directory listing. Returns an empty inventory with a reason when the accession is
    not a GSE or GEO cannot be reached; it never raises, because an unlistable deposit is a reason to
    take the pipeline route rather than a failure of the study.
    """
    base = series_suppl_url(accession)
    if not base:
        return DepositInventory(unavailable_reason=f"{accession or 'the accession'} is not a GEO series id")

    fetch = fetcher or _http_fetch_text
    acc = accession.strip().upper()

    # BOTH listings, always, and this is the correction that running step 1 against the real
    # deposits forced. `filelist.txt` describes the members of `_RAW.tar` and NOTHING else, so a
    # series-level file deposited beside the tar is invisible to it. GSE274331 is exactly that
    # shape: five per-sample bigwigs inside the tar, and `GSE274331_TPMs_H2AS40-KD.xlsx` beside it,
    # which is the only file in that deposit worth reproducing from. Returning on the filelist alone
    # dropped it silently.
    #
    # Two requests for a whole deposit, still far short of the one-per-sample walk this avoids.
    rows: list[dict] = []
    manifest_url = filelist_url(acc)
    if manifest_url:
        try:
            rows = parse_filelist(await fetch(manifest_url))
        except Exception:
            rows = []

    names: list[str] = []
    listing_failed = False
    try:
        names = parse_dir_listing(await fetch(base))
    except Exception:
        listing_failed = True
        logger.info("GEO supplementary listing unreachable for %s", acc)

    if not rows and not names:
        reason = (
            f"GEO did not return a supplementary listing for {acc}"
            if listing_failed
            else f"GEO listed no supplementary files for {acc}"
        )
        return DepositInventory(unavailable_reason=reason)

    def _entry(filename: str, size_bytes: int | None, deposited_type: str | None) -> DepositEntry:
        gsm = _gsm_of(filename)
        return DepositEntry(
            filename=filename,
            # A per-sample file is served from its own GSM directory, not the series one.
            url=(f"{sample_suppl_url(gsm)}{filename}" if gsm else f"{base}{filename}"),
            classification=classify_deposit_filename(filename, deposited_type),
            level="sample" if gsm else "series",
            gsm=gsm,
            size_bytes=size_bytes,
            deposited_type=deposited_type,
        )

    # Filelist rows first so that on a filename present in both, the richer row wins: the directory
    # listing states neither size nor type, and step 5's download cap is enforced against the size.
    by_name: dict[str, DepositEntry] = {}
    for r in rows:
        by_name[r["filename"]] = _entry(r["filename"], r["size_bytes"], r["deposited_type"])
    for name in names:
        by_name.setdefault(name, _entry(name, None, None))

    entries = list(by_name.values())
    return DepositInventory(
        entries=entries,
        triplets=group_triplets(entries),
        source="filelist+directory" if rows and names else ("filelist" if rows else "directory"),
    )
