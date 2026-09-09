"""change_7.1 section 1: describe a deposit through the archive it actually lives in.

Capability discovery had one implementation, GEO's, and every accession went through it. An EGA
study therefore came back "no GEO entry, no raw data, no processed data, no metadata", which is not
a cautious answer: it is a false statement about a paper that deposited 54 samples and 108 FASTQ
files. **An unsupported archive is a limit of ours, never an absence of theirs.**

**Existence, access and support are three separate facts.** ``EGAD00001005044`` exists, is
controlled, and cannot be acquired by bioAF. A single tri-state cannot hold that, and the half it
drops is the half the reader needs: the authors published their reads, and we still cannot run
them. The reproduction is blocked; the paper is not deficient.

**Every archive answers the same questions.** That is what lets the paper-level checklist rows be
an aggregate over deposits rather than a report on whichever accession happened to be scoped.

**Absence cannot be read off a status code here.** EGA answers ``200`` with ``[]`` for an accession
it does not hold, so an empty listing is the established absence and a transport failure is
UNKNOWN. Getting that backwards is how a timeout becomes a finding about the authors.

Cheap by construction: two public metadata calls per EGA deposit, no credentials, no model. This
runs on every paper read, including the ones nobody approves.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

logger = logging.getLogger("bioaf.archive_discovery")

YES = "yes"
NO = "no"
UNKNOWN = "unknown"

Fetcher = Callable[[str], Awaitable[str]]

GEO = "geo"
EGA = "ega"
SRA = "sra"
ARRAYEXPRESS = "arrayexpress"
OTHER = "other"

# How the data may be reached, which is not the same question as whether it is there.
PUBLIC = "public"
CONTROLLED = "controlled"
UNAVAILABLE = "unavailable"
ACCESS_UNKNOWN = "unknown"
NOT_ATTEMPTED = "not_attempted"

_EGA_METADATA = "https://metadata.ega-archive.org"

# Which archives bioAF can actually acquire data FROM, as opposed to merely read about. GEO and SRA
# have download paths (the deposit route and fetchngs); EGA does not, at any access level.
_ACQUIRABLE = (GEO, SRA)

# A study can register more deposits than a read-time budget can describe. Five covers every real
# paper seen so far and bounds the call count; the rest are named without being described.
_MAX_EGA_DATASETS = 5

_ARCHIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (GEO, re.compile(r"^(GSE|GSM|GPL|GDS)\d+$", re.I)),
    (EGA, re.compile(r"^EGA[SDFP]\d+$", re.I)),
    (SRA, re.compile(r"^(PRJNA|PRJEB|PRJDB|SRP|ERP|DRP|SRX|ERX|SRR|ERR|SRS)\d+$", re.I)),
    (ARRAYEXPRESS, re.compile(r"^E-[A-Z]{4}-\d+$", re.I)),
)

# Extensions that ARE the sequencing reads, and extensions that are a derived table. A dataset of
# nothing but the first holds no matrix to reproduce from, which is a real answer rather than a gap.
_RAW_EXTENSIONS = ("fastq", "fq", "bam", "cram", "sra", "fasta")
_PROCESSED_EXTENSIONS = ("tsv", "csv", "txt", "xlsx", "xls", "h5", "h5ad", "rds", "mtx", "loom")


# Accessions that name one SAMPLE inside a deposit rather than a deposit. A GSM belongs to its GSE
# and an EGAF to its EGAD; neither is something a route can be pointed at, and describing one costs
# a full round of lookups to establish nothing.
_SAMPLE_PATTERN = re.compile(r"^(GSM|EGAF|SRR|ERR|DRR|SRS|SAMN|SAMEA)\d+$", re.I)


def is_sample_accession(accession: str) -> bool:
    """Whether an accession names a sample or file rather than a deposit."""
    return bool(_SAMPLE_PATTERN.match((accession or "").strip()))


def classify_archive(accession: str) -> str:
    """Which archive an accession names, or ``OTHER`` when nothing recognises it."""
    acc = (accession or "").strip()
    for archive, pattern in _ARCHIVE_PATTERNS:
        if pattern.match(acc):
            return archive
    return OTHER


def _deposit(accession: str, archive: str, **overrides) -> dict:
    """A deposit answered as far as it could be, with every question present.

    Defaults are UNKNOWN rather than NO so a code path that forgets to answer degrades into "we
    could not find out" instead of inventing an absence.
    """
    base = {
        "archive": archive,
        "accession": accession,
        "provenance": None,
        # The deposit a run would actually use. Scoping decides what runs; it does not license a
        # report that the other deposits are not there.
        "scoped": False,
        "exists": UNKNOWN,
        "access": ACCESS_UNKNOWN,
        "supported": NO,
        "raw_data": UNKNOWN,
        "preprocessed_data": UNKNOWN,
        "sample_metadata": UNKNOWN,
        "evidence": None,
        "failure_reason": None,
    }
    base.update(overrides)
    return base


async def _json(url: str, fetcher: Fetcher):
    """Parsed JSON from a metadata endpoint, or None when it could not be had.

    A maintenance page is a discovery failure exactly like a dropped connection is; neither is
    evidence about the deposit.
    """
    try:
        body = await fetcher(url)
    except Exception as exc:  # noqa: BLE001 - a lookup failure is UNKNOWN, never an absence
        logger.info("EGA metadata fetch failed for %s: %s", url, exc)
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        logger.info("EGA metadata at %s was not JSON", url)
        return None


def _file_answers(files: list[dict] | None) -> tuple[str, str, str | None]:
    """(raw_data, preprocessed_data, evidence) from a dataset's public file listing.

    The listing is public even where the bytes are not, which is what makes "the reads exist and we
    cannot fetch them" an evidenced statement rather than an inference.
    """
    if files is None:
        return UNKNOWN, UNKNOWN, None
    if not files:
        return NO, NO, "the EGA dataset lists no files"

    # "fastq.gz" is a compression wrapper around "fastq"; the part that says what the file IS comes
    # first, and matching on the whole string would miss every gzipped deposit.
    extensions = [str(f.get("extension") or "").lower() for f in files if isinstance(f, dict)]
    stems = [e.split(".")[0] for e in extensions]
    raw = [e for e, stem in zip(extensions, stems, strict=True) if stem in _RAW_EXTENSIONS]
    processed = [e for e, stem in zip(extensions, stems, strict=True) if stem in _PROCESSED_EXTENSIONS]

    if raw:
        kind = sorted({e for e in raw})[0]
        evidence = f"EGA lists {len(raw)} {kind} file(s) for this dataset"
    else:
        evidence = f"EGA lists {len(extensions)} file(s), none of them sequencing reads"
    return (
        YES if raw else NO,
        YES if processed else NO,
        evidence,
    )


async def describe_ega_deposit(accession: str, *, fetcher: Fetcher) -> dict:
    """One EGA study or dataset, described from public metadata. Never raises.

    bioAF has no EGA download client, so ``supported`` is NO whatever the access type says. That is
    a statement about bioAF, and the checklist renders it as one.
    """
    acc = (accession or "").strip()
    is_dataset = acc.upper().startswith("EGAD")
    url = f"{_EGA_METADATA}/datasets/{acc}" if is_dataset else f"{_EGA_METADATA}/studies/{acc}/datasets"

    payload = await _json(url, fetcher)
    if payload is None:
        return _deposit(
            acc,
            EGA,
            failure_reason=f"bioAF could not read EGA's public record for {acc}",
        )

    datasets = [payload] if isinstance(payload, dict) else [d for d in payload if isinstance(d, dict)]
    if not datasets:
        # 200 with an empty list. EGA holds no such accession, which IS the answer.
        return _deposit(
            acc,
            EGA,
            exists=NO,
            access=UNAVAILABLE,
            raw_data=NO,
            preprocessed_data=NO,
            sample_metadata=NO,
            evidence=f"EGA has no released dataset for {acc}",
        )

    described = datasets[:_MAX_EGA_DATASETS]
    access_types = {str(d.get("access_type") or "").lower() for d in described}
    access = CONTROLLED if CONTROLLED in access_types else (PUBLIC if access_types == {PUBLIC} else ACCESS_UNKNOWN)
    if all(d.get("is_released") is False for d in described):
        access = UNAVAILABLE

    samples = sum(int(d.get("num_samples") or 0) for d in described)

    raw_answers: list[str] = []
    processed_answers: list[str] = []
    file_evidence: list[str] = []
    for dataset in described:
        dataset_id = str(dataset.get("accession_id") or "").strip()
        if not dataset_id:
            continue
        files = await _json(f"{_EGA_METADATA}/datasets/{dataset_id}/files", fetcher)
        raw, processed, evidence = _file_answers(files if isinstance(files, list) else None)
        raw_answers.append(raw)
        processed_answers.append(processed)
        if evidence:
            file_evidence.append(evidence)

    def _aggregate(answers: list[str]) -> str:
        """YES beats NO beats UNKNOWN: one dataset holding reads means the study holds reads, and a
        single unreadable listing must not erase a positive answer from its sibling."""
        if YES in answers:
            return YES
        if NO in answers:
            return NO
        return UNKNOWN

    ids = ", ".join(str(d.get("accession_id") or "?") for d in described)
    evidence = f"EGA dataset {ids} is {access} access"
    if samples:
        evidence += f" and registers {samples} sample(s)"
    if file_evidence:
        evidence += "; " + "; ".join(file_evidence)

    return _deposit(
        acc,
        EGA,
        exists=YES,
        access=access,
        supported=NO,  # bioAF has no EGA download client, controlled or not
        raw_data=_aggregate(raw_answers),
        preprocessed_data=_aggregate(processed_answers),
        sample_metadata=YES if samples else UNKNOWN,
        evidence=evidence,
    )


async def describe_geo_deposit(accession: str, *, fetcher: Fetcher) -> dict:
    """One GEO series, described exactly as step 13 described it. Never raises.

    The series matrix answers TWO questions in one fetch (the record exists, because it parsed, and
    it carries per-sample metadata, because it listed samples), and the manifest service already
    caches the multi-platform fallback a combined-matrix 404 needs.

    **Existence comes from the series matrix, never from the supplementary listing.** A failed
    listing of ``.../suppl/`` says nothing about whether the record is there, and deriving existence
    from it read "GEO entry: NO" for a study that is plainly published.
    """
    from app.services.literature.accession_manifest_service import AccessionManifestService, ManifestResult
    from app.services.literature.deposit_inventory_service import list_deposit
    from app.services.deposit_selection import selectable

    acc = (accession or "").strip()
    try:
        manifest = await AccessionManifestService.fetch_manifest(acc, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001 - the service does not raise, but discovery must not either
        logger.info("series matrix discovery failed for %s: %s", acc, exc)
        manifest = ManifestResult(unavailable_reason="bioAF could not reach GEO to read this study's record")

    evidence: list[str] = []
    if manifest.samples:
        exists = YES
        sample_metadata = YES
        evidence.append(f"GEO published a series record for {acc} describing {len(manifest.samples)} sample(s)")
        failure_reason = None
    else:
        exists = UNKNOWN
        sample_metadata = UNKNOWN
        failure_reason = manifest.unavailable_reason or f"GEO returned no series record for {acc}"

    rows, ena_failure = await _ena_rows(acc, fetcher, manifest)
    if rows is None:
        raw_data = UNKNOWN
        failure_reason = failure_reason or ena_failure
    else:
        raw_data, raw_evidence = _raw_read_answer(rows)
        evidence.append(raw_evidence)

    inventory = await list_deposit(acc, fetcher=fetcher)
    if inventory.unavailable_reason:
        # An unlistable directory is a discovery failure. It says nothing about whether the record
        # exists, which is why that question was answered from the series matrix instead.
        preprocessed = UNKNOWN
        failure_reason = failure_reason or inventory.unavailable_reason
    else:
        usable = selectable(inventory.entries)
        preprocessed = YES if usable else NO
        evidence.append(
            f"{len(usable)} of {len(inventory.entries)} deposited file(s) could serve as a reproduction input"
            if usable
            else f"GEO lists {len(inventory.entries)} supplementary file(s), none holding per-feature values"
        )

    return _deposit(
        acc,
        GEO,
        exists=exists,
        access=PUBLIC if exists == YES else ACCESS_UNKNOWN,
        supported=YES,
        raw_data=raw_data,
        preprocessed_data=preprocessed,
        sample_metadata=sample_metadata,
        evidence="; ".join(evidence) or None,
        failure_reason=failure_reason,
    )


async def describe_sra_deposit(accession: str, *, fetcher: Fetcher) -> dict:
    """An SRA/ENA study, which publishes reads and no processed matrix of its own."""
    acc = (accession or "").strip()
    rows, failure = await _ena_rows(acc, fetcher, None)
    if rows is None:
        return _deposit(acc, SRA, supported=YES, failure_reason=failure)
    raw, evidence = _raw_read_answer(rows)
    return _deposit(
        acc,
        SRA,
        exists=YES if rows else NO,
        access=PUBLIC,
        supported=YES,
        raw_data=raw,
        # ENA holds reads. A processed matrix, when a paper has one, lives somewhere else.
        preprocessed_data=NO,
        sample_metadata=YES if any((r.get("sample_title") or "").strip() for r in rows) else UNKNOWN,
        evidence=evidence,
    )


def _unsupported_deposit(accession: str, archive: str) -> dict:
    """An accession bioAF cannot look up. UNKNOWN, and the reason names the archive.

    Answering NO here would report our own gap as a fact about the paper, which is the precise
    error this change exists to remove.
    """
    named = archive if archive != OTHER else "an archive bioAF does not recognise"
    return _deposit(
        accession,
        archive,
        failure_reason=f"bioAF cannot look up deposits in {named}, so what {accession} holds is unknown",
    )


def _raw_read_answer(rows: list[dict]) -> tuple[str, str]:
    """Whether there are raw reads something could actually fetch, and the evidence for it.

    **Registered is not the same as available**, measured live on GSE96583 (2026-09-07): ENA returns
    run rows and a read count with ZERO ``fastq_bytes``, because the study deposits 10x BAMs rather
    than FASTQ. Answering YES off the run rows alone would put a route on the checklist that nothing
    can run.
    """
    if not rows:
        return NO, "ENA lists no sequencing runs for this study"
    with_fastq = [r for r in rows if (r.get("fastq_bytes") or "").strip()]
    if not with_fastq:
        return NO, (
            f"ENA lists {len(rows)} run(s) for this study, none of which publishes FASTQ files "
            "(a study can register its runs and deposit aligned reads instead)"
        )
    return YES, f"ENA publishes FASTQ files for {len(with_fastq)} of {len(rows)} run(s)"


async def _ena_rows(accession: str, fetch: Fetcher, manifest) -> tuple[list[dict] | None, str | None]:
    """The ENA run rows for this study, or a reason we could not get them.

    Reuses ``accession_manifest_service``'s ENA client rather than adding a second one. A GEO series
    is not itself queryable at ENA; its SRA study is, and the manifest resolved that link already,
    so re-deriving it would be a second round trip for an answer we are holding.
    """
    from app.services.literature.accession_manifest_service import _ena_filereport_url, parse_ena_filereport

    target = accession
    if manifest is not None and manifest.samples:
        exp = next((s.get("experiment_accession") for s in manifest.samples if s.get("experiment_accession")), None)
        target = exp or accession
    try:
        tsv = await fetch(_ena_filereport_url(target))
    except Exception as exc:  # noqa: BLE001 - a discovery failure is UNKNOWN, never an absence
        logger.info("ENA availability check failed for %s: %s", target, exc)
        return None, "bioAF could not reach ENA to check whether raw reads are published"
    return parse_ena_filereport(tsv), None


async def describe_deposit(accession: str, *, provenance: str, scoped: bool, fetcher: Fetcher) -> dict:
    """One deposit, described by whichever archive it lives in.

    ``provenance`` records whether the accession was requested by a person or extracted from the
    paper, and ``scoped`` marks the one a run would actually use. Both travel with the answer,
    because "the paper also deposited this" and "this is what we are about to fetch" are different
    claims and the report makes both.
    """
    acc = (accession or "").strip()
    archive = classify_archive(acc)
    if archive == GEO:
        deposit = await describe_geo_deposit(acc, fetcher=fetcher)
    elif archive == EGA:
        deposit = await describe_ega_deposit(acc, fetcher=fetcher)
    elif archive == SRA:
        deposit = await describe_sra_deposit(acc, fetcher=fetcher)
    else:
        deposit = _unsupported_deposit(acc, archive)
    deposit["provenance"] = provenance
    deposit["scoped"] = scoped
    return deposit
