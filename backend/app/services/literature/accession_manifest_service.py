"""Sample-manifest fetch for the lit_validation Level-3 gate (sample selection by recognition).

At the C1 gate a scientist confirms which of a validation study's samples are the test arm vs the
reference arm. Instead of hand-typing accession tokens from the paper, the gate shows recognizable
rows (title + condition) built from the study's public deposit metadata. This service resolves a
deposited accession into that per-sample manifest:

- GEO series (``GSE...``): the series-matrix carries ``!Sample_title`` + ``!Sample_characteristics_ch1``
  (the human-facing recognition signal) and per-sample ``!Sample_relation`` links to the SRA
  experiment (``SRX``) + BioSample (``SAMN``). Run accessions are not in the matrix, so we resolve the
  series' ``!Series_relation`` SRA study and join it (by experiment accession) from ENA to fill them.
- ENA/SRA study, project, experiment, run or sample: the ENA portal ``filereport`` TSV
  (``result=read_run``) lists one row per run; we de-dupe to one entry per experiment (the biological
  sample the scientist recognizes and picks).

Mirrors ``ground_truth_fetch_service``: the HTTP boundary is a small injectable async callable so the
resolution is unit-testable without network, and the default fetcher uses httpx and transparently
gunzips ``.gz``. Best-effort by design: any failure yields an empty manifest + a human-readable reason
(never raises), so the gate degrades to today's free-text entry. See
``local/ui_rework_v2/plan-sample-selection-and-study-naming.md`` (block 1).
"""

from __future__ import annotations

import gzip
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("bioaf.accession_manifest")

Fetcher = Callable[[str], Awaitable[str]]

_ENA_FILEREPORT = "https://www.ebi.ac.uk/ena/portal/api/filereport"
_ENA_FIELDS = (
    "run_accession",
    "experiment_accession",
    "sample_accession",
    "sample_title",
    "experiment_title",
    "library_strategy",
)
_GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo/series"
_TIMEOUT = httpx.Timeout(30.0)

# Accession-type patterns. ENA/SRA/DDBJ run/experiment/sample/study accessions are
# {SR,ER,DR}{R,X,S,P}<digits>; INSDC projects are PRJ{NA,EB,DB}<digits>; BioSamples are SAM{N,EA,D}<id>.
_GSE_RE = re.compile(r"GSE(\d+)", re.IGNORECASE)
_ENA_STUDY_RE = re.compile(r"(SR|ER|DR)[RXSP]\d+", re.IGNORECASE)
_PRJ_RE = re.compile(r"PRJ(NA|EB|DB)\d+", re.IGNORECASE)
_SAM_RE = re.compile(r"SAM(N|EA|D)\w+", re.IGNORECASE)
# Within a relation URL: the SRA experiment (SRX/ERX/DRX), an SRA study (SRP/ERP/DRP), a BioSample.
_SRX_RE = re.compile(r"(SR|ER|DR)X\d+", re.IGNORECASE)
_SRP_RE = re.compile(r"(SR|ER|DR)P\d+", re.IGNORECASE)


@dataclass
class ManifestResult:
    """A study's per-sample manifest, or an explicit unavailable reason (never an exception).

    Each entry is ``{experiment_accession, run_accession, sample_accession, title, condition,
    library_strategy}``. The picker stores ``experiment_accession`` as the scientist's stable pick;
    ``title`` + ``condition`` are the recognition signal; ``run_accession`` / ``sample_accession`` are
    informational; ``library_strategy`` is the depositor's own controlled statement of what the data
    IS, which is what routes a plan when the paper's prose names more than one assay.
    """

    samples: list[dict] = field(default_factory=list)
    unavailable_reason: str | None = None


def geo_series_matrix_url(accession: str) -> str | None:
    """Build the GEO series-matrix URL for a GSE accession, or None if not a GSE id.

    GEO groups series in ``GSE<prefix>nnn/`` where the last three digits are masked (GSE309060 ->
    GSE309nnn; GSE12 -> GSEnnn), mirroring ``geo_suppl_dir_url`` in ground_truth_fetch_service."""
    m = re.fullmatch(r"GSE(\d+)", (accession or "").strip().upper())
    if not m:
        return None
    num = m.group(1)
    stub = f"GSE{num[:-3]}nnn" if len(num) > 3 else "GSEnnn"
    return f"{_GEO_FTP}/{stub}/GSE{num}/matrix/GSE{num}_series_matrix.txt.gz"


def geo_matrix_dir_url(accession: str) -> str | None:
    """The series-matrix FOLDER for a GSE accession, or None if not a GSE id.

    GEO publishes the combined ``GSE<n>_series_matrix.txt.gz`` only when a series used ONE platform
    (GPL, the sequencing instrument). A series spanning two instruments is published as one matrix
    per platform and no combined file, so the folder is the only way to learn what exists.
    """
    url = geo_series_matrix_url(accession)
    if url is None:
        return None
    return url.rsplit("/", 1)[0] + "/"


_MATRIX_HREF_RE = re.compile(r'href="([^"/]+_series_matrix\.txt\.gz)"', re.IGNORECASE)


def parse_matrix_directory(html: str) -> list[str]:
    """The series-matrix filenames an Apache index page lists, in page order (pure).

    Anchored on the ``_series_matrix.txt.gz`` suffix and on the href having no path separator, so the
    parent-directory link and the study's other artefacts (RAW tarballs, filelists) cannot match.
    """
    seen: list[str] = []
    for name in _MATRIX_HREF_RE.findall(html or ""):
        if name not in seen:
            seen.append(name)
    return seen


def _ena_filereport_url(accession: str) -> str:
    """Build the ENA portal filereport (read_run TSV) URL for any SRA/ENA/INSDC accession."""
    fields = ",".join(_ENA_FIELDS)
    return f"{_ENA_FILEREPORT}?accession={accession}&result=read_run&fields={fields}&format=tsv&download=false"


def parse_ena_filereport(tsv: str) -> list[dict]:
    """Parse an ENA filereport TSV into row dicts keyed by header (pure)."""
    lines = [ln for ln in (tsv or "").splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("\t")]
    rows: list[dict] = []
    for line in lines[1:]:
        cells = line.split("\t")
        rows.append({header[i]: (cells[i].strip() if i < len(cells) else "") for i in range(len(header))})
    return rows


def _series_matrix_values(line: str) -> list[str]:
    """Tab-separated, double-quoted values of a series-matrix ``!Key`` line, minus the key."""
    return [cell.strip().strip('"') for cell in line.split("\t")[1:]]


def _first_match(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return m.group(0) if m else ""


def parse_series_matrix(text: str) -> tuple[list[dict], str | None]:
    """Parse a GEO series-matrix into (samples, series_sra_accession).

    ``samples`` is one entry per sample column: ``{title, condition, experiment_accession,
    sample_accession, run_accession, library_strategy}`` (the last two are "" here; the caller fills
    them from ENA).
    ``series_sra_accession`` is the study-level SRA/BioProject accession from ``!Series_relation``
    (SRA study preferred), used to resolve run accessions. Samples are COLUMNS in a series matrix.
    """
    titles: list[str] = []
    conditions_lines: list[list[str]] = []
    sra_relation: list[str] = []
    biosample_relation: list[str] = []
    series_relations: list[str] = []

    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("!Sample_title\t"):
            titles = _series_matrix_values(line)
        elif line.startswith("!Sample_characteristics_ch1\t"):
            conditions_lines.append(_series_matrix_values(line))
        elif line.startswith("!Sample_relation\t"):
            values = _series_matrix_values(line)
            if any(_SRX_RE.search(v) for v in values):
                sra_relation = values
            elif any(_SAM_RE.search(v) for v in values):
                biosample_relation = values
        elif line.startswith("!Series_relation\t"):
            series_relations.extend(_series_matrix_values(line))

    # Prefer an SRA study accession (directly the fetchngs source), then a BioProject. Scan the whole
    # relation set for an SRA study before falling back, since GEO lists BioProject first.
    series_sra: str | None = None
    for value in series_relations:
        acc = _first_match(_SRP_RE, value)
        if acc:
            series_sra = acc
            break
    if not series_sra:
        for value in series_relations:
            acc = _first_match(_PRJ_RE, value)
            if acc:
                series_sra = acc
                break

    samples: list[dict] = []
    for i, title in enumerate(titles):
        condition = "; ".join(line[i] for line in conditions_lines if i < len(line) and line[i])
        samples.append(
            {
                "title": title,
                "condition": condition,
                "experiment_accession": _first_match(_SRX_RE, sra_relation[i]) if i < len(sra_relation) else "",
                "sample_accession": _first_match(_SAM_RE, biosample_relation[i]) if i < len(biosample_relation) else "",
                "run_accession": "",
                # A series matrix does not record the assay; the ENA join below fills it.
                "library_strategy": "",
            }
        )
    return samples, series_sra


def dominant_library_strategy(samples: list[dict]) -> str | None:
    """The one ``library_strategy`` an accession's samples agree on, or None.

    A deposit carrying two assays is exactly as compound as the paper prose it would be overriding
    (GSE213770 deposits Bisulfite-Seq beside RNA-Seq), so disagreement yields no answer rather than
    whichever run happened to be listed first. Blanks are absent evidence, not a third opinion.
    """
    seen: dict[str, str] = {}
    for sample in samples or []:
        raw = str((sample or {}).get("library_strategy") or "").strip()
        if raw:
            seen.setdefault(raw.lower(), raw)
    if len(seen) != 1:
        return None
    return next(iter(seen.values()))


async def _http_fetch_text(url: str) -> str:
    """Default fetcher: GET the URL and return text, transparently gunzipping ``.gz`` payloads."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        if url.lower().endswith(".gz"):
            return gzip.decompress(r.content).decode("utf-8", errors="replace")
        return r.text


def _classify(accession: str) -> str:
    a = (accession or "").strip().upper()
    if re.fullmatch(r"GSE\d+", a):
        return "geo"
    if _ENA_STUDY_RE.fullmatch(a) or _PRJ_RE.fullmatch(a) or _SAM_RE.fullmatch(a):
        return "ena"
    return "unknown"


def _entries_from_ena_rows(rows: list[dict]) -> list[dict]:
    """Collapse ENA read_run rows to one entry per experiment (the biological sample the scientist
    picks); multiple runs of one experiment share an entry. Falls back to run/sample as the dedupe
    key when experiment_accession is absent."""
    entries: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        exp = (row.get("experiment_accession") or "").strip()
        run = (row.get("run_accession") or "").strip()
        sample = (row.get("sample_accession") or "").strip()
        key = exp or run or sample
        if not key or key in seen:
            continue
        seen.add(key)
        title = (row.get("sample_title") or row.get("experiment_title") or run or exp).strip()
        entries.append(
            {
                "experiment_accession": exp,
                "run_accession": run,
                "sample_accession": sample,
                "title": title,
                "condition": "",
                "library_strategy": (row.get("library_strategy") or "").strip(),
            }
        )
    return entries


class AccessionManifestService:
    @staticmethod
    async def fetch_manifest(accession: str, *, fetcher: Fetcher | None = None) -> ManifestResult:
        """Resolve a deposited accession into a per-sample manifest for the Level-3 picker.

        Never raises: any fetch failure / unknown accession type returns an empty manifest with a
        human-readable ``unavailable_reason`` so the gate can fall back to free-text entry."""
        acc = (accession or "").strip()
        if not acc:
            return ManifestResult(unavailable_reason="This study has no deposited accession.")
        fetch = fetcher or _http_fetch_text
        kind = _classify(acc)
        if kind == "geo":
            return await AccessionManifestService._fetch_geo(acc, fetch)
        if kind == "ena":
            return await AccessionManifestService._fetch_ena(acc, fetch)
        return ManifestResult(unavailable_reason=f"Unrecognized accession type: {acc}.")

    @staticmethod
    async def _fetch_ena(accession: str, fetch: Fetcher) -> ManifestResult:
        try:
            tsv = await fetch(_ena_filereport_url(accession))
        except Exception as exc:
            logger.info("ENA filereport fetch failed for %s: %s", accession, exc)
            return ManifestResult(unavailable_reason="Could not reach ENA to list this study's samples.")
        entries = _entries_from_ena_rows(parse_ena_filereport(tsv))
        if not entries:
            return ManifestResult(unavailable_reason=f"ENA returned no runs for {accession}.")
        return ManifestResult(samples=entries)

    @staticmethod
    async def _fetch_geo(accession: str, fetch: Fetcher) -> ManifestResult:
        url = geo_series_matrix_url(accession)
        if not url:  # defensive; _classify already gated on GSE
            return ManifestResult(unavailable_reason=f"Unrecognized accession type: {accession}.")

        # The combined file first: it is what a single-platform series publishes, which is the large
        # majority, and it answers in one request. Only when it is absent do we pay for a directory
        # listing, and its absence is the multi-platform case rather than an outage.
        try:
            texts = [await fetch(url)]
            missing = 0
        except Exception as exc:
            logger.info("GEO combined series-matrix unavailable for %s (%s); listing the folder", accession, exc)
            texts, missing, failed = await AccessionManifestService._fetch_platform_matrices(accession, fetch)
            if texts is None:
                return ManifestResult(unavailable_reason=failed)

        samples: list[dict] = []
        series_sra: str | None = None
        for text in texts:
            parsed, sra = parse_series_matrix(text)
            samples.extend(parsed)
            series_sra = series_sra or sra
        if not samples:
            return ManifestResult(unavailable_reason=f"GEO series matrix for {accession} listed no samples.")

        # Best-effort: fill run accessions by joining the series' SRA study from ENA on the experiment
        # accession. Resolved once for the whole series, not per platform file, because the SRA study
        # is a property of the series. A failure here leaves run_accession empty but keeps the
        # recognizable manifest.
        if series_sra:
            try:
                tsv = await fetch(_ena_filereport_url(series_sra))
                run_by_exp: dict[str, str] = {}
                strategy_by_exp: dict[str, str] = {}
                for row in parse_ena_filereport(tsv):
                    exp = (row.get("experiment_accession") or "").strip()
                    if exp and exp not in run_by_exp:
                        run_by_exp[exp] = (row.get("run_accession") or "").strip()
                        strategy_by_exp[exp] = (row.get("library_strategy") or "").strip()
                for sample in samples:
                    exp = sample["experiment_accession"]
                    if not sample["run_accession"] and exp in run_by_exp:
                        sample["run_accession"] = run_by_exp[exp]
                    if not sample["library_strategy"]:
                        sample["library_strategy"] = strategy_by_exp.get(exp, "")
            except Exception as exc:
                logger.info("GEO->ENA run-accession enrichment failed for %s: %s", accession, exc)

        # A partial manifest is more useful than none at a picker, and dangerous if it is not
        # announced: an arm scoped from a list quietly missing samples is wrong in a way nobody sees.
        partial = (
            f"Only {len(texts)} of {len(texts) + missing} platform files could be read for {accession}, "
            "so this list may be missing samples."
            if missing
            else None
        )
        return ManifestResult(samples=samples, unavailable_reason=partial)

    @staticmethod
    async def _fetch_platform_matrices(accession: str, fetch: Fetcher) -> tuple[list[str] | None, int, str | None]:
        """Every per-platform series matrix GEO published, as (texts, missing_count, failure_reason).

        ``texts`` is None only when nothing could be read at all, and ``failure_reason`` then says
        which of the two things went wrong: GEO was unreachable, or GEO answered and has published no
        series matrix for this study. Those were indistinguishable before, and reporting the second
        as the first blamed NCBI for a URL this code built wrong.
        """
        dir_url = geo_matrix_dir_url(accession)
        if not dir_url:
            return None, 0, f"Unrecognized accession type: {accession}."
        try:
            listing = await fetch(dir_url)
        except Exception as exc:
            logger.info("GEO matrix-folder listing failed for %s: %s", accession, exc)
            return None, 0, "Could not reach GEO to list this study's samples."

        names = parse_matrix_directory(listing)
        if not names:
            return None, 0, f"GEO has published no series matrix for {accession}."

        texts: list[str] = []
        missing = 0
        for name in names:
            try:
                texts.append(await fetch(dir_url + name))
            except Exception as exc:
                missing += 1
                logger.info("GEO platform matrix %s unreadable for %s: %s", name, accession, exc)
        if not texts:
            return None, 0, "Could not reach GEO to list this study's samples."
        return texts, missing, None
