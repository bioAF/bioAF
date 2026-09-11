"""change_7.1 section 2: the paper's own attachments, found independently of any deposit.

There was no supplement discovery. Code availability came from what a model read in the prose, the
deposit inventory listed GEO and nothing else, and an article's attachments were never looked at.
For Groff et al. that lost a 54-row sample metadata table, the authors' complete R analysis, and
the differential-results table, every one of them public the whole time. The study was then
reported as having no accessible data.

**A reference and an attachment are different things.** The prose says "Supplemental File S2"; the
bytes live in a bundle behind a separate request. Naming the first is free at read time, because
the JATS is already fetched and thrown away; resolving it to the second costs a download. The
inventory therefore carries both states and always says which one a row is in.

**An unresolved reference is a discovery limitation, never an established absence.** "We have not
fetched it yet" and "it is not there" are different statements, and only one of them is a finding
about the authors.

**Role comes from inspected content, not from the filename.** A file called ``embryo_metadata.txt``
holding DESeq2 output is a results table. The name is the author's habit; the header is evidence.
The distinction that matters most is the last one below: a results table is NOT the sample-level
matrix needed to rerun the analysis that produced it, and accepting it as one would produce a
"reproduction" that reads its own answer back.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile

from app.services.validation_acquisition_outcome import MAX_ATTEMPTS

logger = logging.getLogger("bioaf.supplement_inventory")

# Where a row came from, which is also how far discovery got with it.
ATTACHED = "attached"  # a media element in the JATS, with a filename
NAMED_IN_TEXT = "named_in_text"  # the prose cites it; nothing has resolved it to a file yet

# change_7.3 section 2: what a row IS, decided from the manifest before any bytes arrive. An index
# page is the publisher's wrapper around the attachments, not one of them, and a figure image in the
# bundle is the article's own figure. Neither is counted as a supplement or retried as one.
KIND_ATTACHMENT = "attachment"
KIND_INDEX = "index"
KIND_REFERENCE = "reference"  # named in the prose with no matching attachment in the manifest
KIND_FIGURE = "figure"

# Where an artifact was named. The four statuses of section 3 start here: identification.
IDENTIFIED_IN_MANIFEST = "article_manifest"
IDENTIFIED_IN_PROSE = "prose"
IDENTIFIED_IN_BUNDLE = "bundle"

# change_7.3 section 3: retrieval is its own status, kept separate from what inspection found.
RETRIEVAL_NOT_ATTEMPTED = "not_attempted"
RETRIEVAL_FAILED = "failed"
RETRIEVAL_RETRIEVED = "retrieved"
RETRIEVAL_NOT_IN_BUNDLE = "not_in_bundle"
RETRIEVAL_STATUSES = (RETRIEVAL_NOT_ATTEMPTED, RETRIEVAL_FAILED, RETRIEVAL_RETRIEVED, RETRIEVAL_NOT_IN_BUNDLE)

# What a resource IS, established by looking inside it.
SAMPLE_METADATA = "sample_metadata"
EXPRESSION_MATRIX = "expression_matrix"
RESULTS_TABLE = "results_table"
CODE = "code"
SUPPORTING_INPUT = "supporting_input"
UNKNOWN_ROLE = "unknown"

# The tri-state the capability checklist uses, imported rather than re-spelled.
YES_ANSWER = "yes"

_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# "Supplemental File S2", "Supplementary Table S1", "Supplemental Data Set S3", "Additional file 2".
# Journals differ on every word except the pattern. The NOUN is kept: "Supplementary Table S1" and
# "Supplemental File S1" are two different artifacts in the same paper, and collapsing both to "File
# S1" merged them into one row. An enumeration ("Supplemental Files S2, S3") names every identifier.
_IDENT = r"S?\d+[A-Za-z]?"
_NAMED_SUPPLEMENT_RE = re.compile(
    r"\b(?:Supplement(?:al|ary)\s+(?P<noun>File|Table|Data\s*Set|Dataset|Material)s?"
    r"|Additional\s+(?P<additional>file|table)s?)"
    rf"\s+(?P<ids>{_IDENT}(?:\s*(?:,|and|&)\s*{_IDENT})*)",
    re.I,
)
_ONE_IDENT_RE = re.compile(rf"\b({_IDENT})\b")

_CANONICAL_NOUN = {
    "file": "File",
    "table": "Table",
    "dataset": "Data Set",
    "data set": "Data Set",
    "material": "Material",
}

# A differential-results table announces itself in its header. These are the DESeq2/edgeR/limma
# column names, and two of them together are conclusive.
_RESULT_COLUMNS = ("log2foldchange", "log2fc", "padj", "pvalue", "p.value", "adj.p.val", "basemean", "logfc")

# Per-sample descriptors. A metadata table is samples down the rows and attributes across.
_METADATA_COLUMNS = (
    "sample",
    "sampletype",
    "condition",
    "treatment",
    "genotype",
    "sex",
    "age",
    "grade",
    "batch",
    "tissue",
    "group",
    "replicate",
    "patient",
    "subject",
    "timepoint",
)

_CODE_MARKERS = ("library(", "import ", "def ", "<-", "```{r", "#!/", "install.packages", "source(")

_TEXTUAL_EXTENSIONS = (".txt", ".tsv", ".csv", ".tab", ".md", ".r", ".rmd", ".py", ".sh", ".ipynb")


def parse_jats_supplements(xml_text: str) -> list[dict]:
    """Every supplement the article names or attaches. Free: the JATS is already in hand.

    Two sources, deliberately merged into one list. ``<supplementary-material>`` gives attachments
    with filenames; the prose gives the identifiers a reader would recognise ("Supplemental File
    S2"), which for many journals is the ONLY place a specific file is named. Groff's JATS attaches
    one ``index.html`` wrapper and names S1, S2 and S3 in the body, so a manifest built from the
    markup alone would find the wrapper and miss the analysis.

    Never raises: a malformed document is a discovery limitation, not a reason to fail a read.

    change_7.3 section 2: **identity is established here, from the manifest, whether or not any
    bytes ever arrive.** A prose citation that matches an attached filename becomes an alias of that
    attachment rather than a second row, so a failed download can no longer turn four attachments
    into eight "supplements". A citation with no match in the manifest stays its own entry: a
    discovery limitation, never a missing file.
    """
    rows: dict[str, dict] = {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.info("could not parse JATS for supplements: %s", exc)
        return []

    for element in root.iter():
        if not element.tag.endswith("supplementary-material"):
            continue
        label = _text_of(element, "label") or _text_of(element, "title") or "Supplementary material"
        for media in element.iter():
            if not (media.tag.endswith("media") or media.tag.endswith("graphic")):
                continue
            href = next((v for k, v in media.attrib.items() if k.endswith("href")), None)
            if not href:
                continue
            # Keyed by the file, because one <supplementary-material> can carry several media and
            # they are different attachments. The label is disambiguated only when it collides, so
            # the common single-file case keeps the caption the reader would recognise.
            taken = {row["label"] for row in rows.values()}
            mimetype = _mimetype_of(media)
            role = next((v for k, v in media.attrib.items() if k.endswith("role")), None)
            rows.setdefault(
                href,
                _new_row(
                    label=label if label not in taken else f"{label} ({href})",
                    filename=href,
                    mimetype=mimetype,
                    source=ATTACHED,
                    kind=_manifest_kind(href, mimetype, role),
                    identified_in=IDENTIFIED_IN_MANIFEST,
                ),
            )

    body_text = " ".join(t for t in root.itertext() if t)
    for citation in _citations(body_text):
        identity = f"reference:{citation['key']}"
        rows.setdefault(
            identity,
            _new_row(
                label=citation["label"],
                filename=None,
                mimetype=None,
                source=NAMED_IN_TEXT,
                kind=KIND_REFERENCE,
                identified_in=IDENTIFIED_IN_PROSE,
                identity=identity,
            ),
        )

    return establish_identity(list(rows.values()))


def establish_identity(rows: list[dict] | None) -> list[dict]:
    """One row per artifact: every prose citation that names an attached file becomes its alias.

    Runs at read time on the manifest and again before retrieval, so a study recorded before this
    existed (its citations and its attachments listed side by side, each "not retrieved") gets the
    same identities when it is resumed. A citation that matches no attachment, or matches more than
    one, stays its own row: ambiguity is not a match.
    """
    upgraded = [_upgraded(dict(row)) for row in rows or [] if isinstance(row, dict)]
    attachments = [row for row in upgraded if row["kind"] == KIND_ATTACHMENT and row.get("filename")]
    kept: list[dict] = []
    for row in upgraded:
        if row["kind"] == KIND_REFERENCE and not row.get("resolved"):
            citations = [c for label in row["references"] for c in _citations(label)]
            target = next((a for a in (_attachment_for(c, attachments) for c in citations) if a is not None), None)
            if target is not None:
                for label in row["references"]:
                    _add_reference(target, label, IDENTIFIED_IN_PROSE)
                continue
        kept.append(row)
    for row in kept:
        row["label"] = _preferred_label(row["references"])
    return kept


_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".gif", ".png", ".tif", ".tiff", ".bmp", ".svg", ".eps")


def _upgraded(row: dict) -> dict:
    """Fill the section 2 and 3 fields on a row recorded before they existed. Never overwrites.

    A legacy row carries its failure as a copied ``failure_reason``; that is read as a failed
    retrieval with no ledger entry, which is what it was.
    """
    filename = row.get("filename")
    if not row.get("references"):
        row["references"] = [row["label"]] if row.get("label") else []
    if "kind" not in row:
        if not filename:
            row["kind"] = KIND_REFERENCE
        elif str(filename).lower().endswith(_IMAGE_EXTENSIONS) and row.get("source") != NAMED_IN_TEXT:
            row["kind"] = (
                KIND_FIGURE if not row.get("mimetype") else _manifest_kind(filename, row.get("mimetype"), None)
            )
        else:
            row["kind"] = _manifest_kind(filename, row.get("mimetype"), None)
    if not row.get("identity"):
        citation = next((c for label in row["references"] for c in _citations(label)), None)
        row["identity"] = filename or (f"reference:{citation['key']}" if citation else f"reference:{row.get('label')}")
    if not row.get("identified_in"):
        row["identified_in"] = [IDENTIFIED_IN_PROSE if row.get("source") == NAMED_IN_TEXT else IDENTIFIED_IN_MANIFEST]
    if not isinstance(row.get("retrieval"), dict):
        if row.get("resolved"):
            status = RETRIEVAL_RETRIEVED
        elif row.get("failure_reason"):
            status = RETRIEVAL_FAILED
        else:
            status = RETRIEVAL_NOT_ATTEMPTED
        row["retrieval"] = {"status": status, "ledger": None}
    return row


def _new_row(
    *,
    label: str,
    filename: str | None,
    mimetype: str | None,
    source: str,
    kind: str,
    identified_in: str,
    identity: str | None = None,
) -> dict:
    return {
        "label": label,
        "filename": filename,
        "mimetype": mimetype,
        "source": source,
        "role": UNKNOWN_ROLE,
        "size_bytes": None,
        "resolved": False,
        "kind": kind,
        # One identity per artifact, whatever happens to its bytes. The filename where the manifest
        # gives one; a canonical citation otherwise.
        "identity": identity or filename,
        "references": [label],
        "identified_in": [identified_in],
        "retrieval": {"status": RETRIEVAL_NOT_ATTEMPTED, "ledger": None},
    }


def _add_reference(row: dict, label: str, identified_in: str) -> None:
    if label not in row["references"]:
        row["references"].append(label)
    if identified_in not in row["identified_in"]:
        row["identified_in"].append(identified_in)


def _manifest_kind(href: str, mimetype: str | None, role: str | None) -> str:
    """An attachment, or the publisher's index page around the attachments.

    Europe PMC marks the real attachments ``xlink:role="associated-file"`` and leaves the HTML
    wrapper unmarked. Most journals set no role at all, so its absence is not evidence of an index:
    only an unmarked HTML page is one.
    """
    if (role or "").strip().lower() == "associated-file":
        return KIND_ATTACHMENT
    if (mimetype or "").lower() in ("text/html", "html") or href.lower().endswith((".html", ".htm")):
        return KIND_INDEX
    return KIND_ATTACHMENT


def _citations(text: str) -> list[dict]:
    """Every supplement the prose cites, one entry per identifier, in order of first mention.

    ``key`` is what makes two spellings one citation ("Supplementary File S2", "Supplemental file
    s2"); ``label`` is the canonical form a reader recognises.
    """
    found: dict[str, dict] = {}
    for match in _NAMED_SUPPLEMENT_RE.finditer(text or ""):
        if match.group("additional"):
            word = match.group("additional").lower()
            noun, prefix = f"additional_{word}", f"Additional {word.title()}"
        else:
            raw = " ".join(match.group("noun").split()).lower()
            canonical = _CANONICAL_NOUN.get(raw, raw.title())
            noun, prefix = canonical.lower().replace(" ", ""), f"Supplemental {canonical}"
        for ident in _ONE_IDENT_RE.findall(match.group("ids")):
            ident = ident.upper()
            key = f"{noun}:{ident}"
            found.setdefault(key, {"key": key, "label": f"{prefix} {ident}", "noun": noun, "ident": ident})
    return list(found.values())


_NOUN_TOKENS = {
    "file": r"files?",
    "table": r"(?:tables?|tab)",
    "dataset": r"(?:data_?sets?|data)",
    "material": r"materials?",
    "additional_file": r"additional_files?",
    "additional_table": r"additional_tables?",
}


def _normalized_name(filename: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (filename or "").lower())


def _filename_pattern(citation: dict) -> re.Pattern[str]:
    """The filename shape a citation's own noun and number produce.

    Publishers mangle names in every direction, but the noun and the number survive: "Supplemental
    File S2" becomes ``..._Supplemental_File_2_...``. The noun is part of the match, so "Table S1"
    never takes "File 1". Springer's ``MOESM2_ESM`` numbering is what "Additional file 2" means.
    """
    number = re.escape(citation["ident"].lower().lstrip("s") or citation["ident"].lower())
    tokens = _NOUN_TOKENS.get(citation["noun"], re.escape(citation["noun"]))
    alternatives = [rf"(?:^|_){tokens}_?s?0*{number}(?:_|$)"]
    if citation["noun"].startswith("additional_"):
        alternatives.append(rf"(?:^|_)moesm0*{number}_esm(?:_|$)")
    return re.compile("|".join(alternatives))


def _attachment_for(citation: dict, candidates: list[dict]) -> dict | None:
    """The one attachment a citation names, or None. Two candidates is ambiguity, not a match, and
    picking one would invent the fact this exists to establish."""
    pattern = _filename_pattern(citation)
    matches = [row for row in candidates if pattern.search(_normalized_name(row.get("filename")))]
    return matches[0] if len(matches) == 1 else None


def _text_of(element, tag: str) -> str | None:
    for child in element.iter():
        if child.tag.endswith(tag):
            text = " ".join(t.strip() for t in child.itertext() if t and t.strip())
            if text:
                return text
    return None


def _mimetype_of(media) -> str | None:
    mime = media.attrib.get("mimetype")
    sub = media.attrib.get("mime-subtype")
    if mime and sub:
        return f"{mime}/{sub}"
    return mime or sub


def extract_docx_text(blob: bytes) -> str | None:
    """The text of a .docx, or None when the blob is not one.

    A DOCX is a zip holding ``word/document.xml``, so this needs no dependency. Groff's Supplemental
    File S2 is 2933 non-empty paragraphs of R Markdown: the paper's entire analysis, stored as an
    opaque blob and never read. Paragraph structure is preserved as newlines, because code whose
    line breaks are gone is not code any more.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            document = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        logger.info("blob is not a readable .docx: %s", exc)
        return None

    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return None

    paragraphs = ["".join(node.text or "" for node in para.iter(f"{_DOCX_NS}t")) for para in root.iter(f"{_DOCX_NS}p")]
    return "\n".join(paragraphs)


def classify_supplement(filename: str, blob: bytes) -> str:
    """What a supplement IS, from its content. Never raises.

    Order matters. A ``.docx`` is opened as a document before anything looks at its extension,
    because the only way to tell the authors' code from a page of figure legends is to read it.
    """
    name = (filename or "").lower()

    if name.endswith(".docx") or blob[:2] == b"PK":
        text = extract_docx_text(blob)
        if text and _looks_like_code(text):
            return CODE

    if name.endswith((".r", ".rmd", ".py", ".ipynb", ".sh")):
        return CODE

    text = _decode(blob)
    if text is None:
        return UNKNOWN_ROLE

    if _looks_like_code(text) and not name.endswith((".txt", ".tsv", ".csv")):
        return CODE

    header = _header_of(text)
    if header is None:
        return UNKNOWN_ROLE

    columns = [c.strip().strip('"').lower() for c in header]
    statistics = [c for c in columns if c in _RESULT_COLUMNS]
    if len(statistics) >= 2:
        # A results table, and specifically NOT an expression matrix: these are one row per gene of
        # an analysis that already ran, not the per-sample values needed to run it again.
        return RESULTS_TABLE

    if any(any(m in c for m in _METADATA_COLUMNS) for c in columns):
        return SAMPLE_METADATA

    # A single statistic column (edgeR's `logFC` with an `FDR` we did not recognise, say) still
    # means this is per-gene OUTPUT, not per-sample input. Falling through to the matrix branch is
    # how a results table gets offered as something to reproduce from, which is the one
    # misclassification that would let a study read its own answer back.
    if statistics:
        return RESULTS_TABLE

    if _looks_like_matrix(text):
        return EXPRESSION_MATRIX

    return SUPPORTING_INPUT if name.endswith(_TEXTUAL_EXTENSIONS) else UNKNOWN_ROLE


def _decode(blob: bytes) -> str | None:
    try:
        text = blob.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None
    return text if text.strip() else None


def _looks_like_code(text: str) -> bool:
    head = text[:20000]
    return sum(1 for marker in _CODE_MARKERS if marker in head) >= 2


def _header_of(text: str) -> list[str] | None:
    first = next((line for line in text.splitlines() if line.strip()), None)
    if first is None:
        return None
    for delimiter in ("\t", ","):
        if delimiter in first:
            return first.split(delimiter)
    return None


def _looks_like_matrix(text: str) -> bool:
    """Rows of numbers under a header of sample names: values per feature per sample.

    Checked on the SECOND row, because a header of sample names is all strings in both shapes and
    only the data tells a matrix from a metadata table.
    """
    lines = [line for line in text.splitlines() if line.strip()][:3]
    if len(lines) < 2:
        return False
    delimiter = "\t" if "\t" in lines[0] else ","
    cells = lines[1].split(delimiter)[1:]
    if len(cells) < 2:
        return False
    numeric = 0
    for cell in cells:
        try:
            float(cell.strip().strip('"'))
            numeric += 1
        except ValueError:
            pass
    return numeric >= len(cells) - 1


# Europe PMC serves an article's attachments as one zip. There is no listing endpoint, which is why
# the manifest at read time comes from the JATS and only the resolution step pays for bytes.
_SUPPLEMENTARY_BUNDLE = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/supplementaryFiles"

# Groff's bundle is 9.4 MB, most of it figure images. A cap keeps one pathological article from
# spending a session's memory on TIFFs.
_MAX_BUNDLE_BYTES = 200 * 1024 * 1024

# change_7.3 section 1: what one retrieval attempt came to. A 404 from the bundle endpoint is
# `not_found` and still retryable: it was observed to be transient, and the article's own manifest
# already establishes that the files exist, so it can never mean they are absent.
RETRIEVED = "retrieved"
NOT_FOUND = "not_found"
FORBIDDEN = "forbidden"
TRANSIENT = "transient"
TOO_LARGE = "too_large"
UNREADABLE = "unreadable"
RETRIEVAL_OUTCOMES = (RETRIEVED, NOT_FOUND, FORBIDDEN, TRANSIENT, TOO_LARGE, UNREADABLE)
_RETRYABLE = (NOT_FOUND, TRANSIENT)

BUNDLE_SOURCE = "europepmc_supplementary_bundle"
BUNDLE_SOURCE_LABEL = "the article's supplementary bundle from Europe PMC"

# Bounded, with backoff, never a fixed-interval loop: the attempt cap is change_7.2 section 3's.
# The waits are seconds rather than that policy's minutes because retrieval runs inside one driver
# tick or one approval request, and a quarter-hour sleep there would stall every other study.
# "Review and resume" is the product path to try again later.
_RETRY_DELAYS = (2.0, 6.0)
_sleep = asyncio.sleep


def _status_of(exc: Exception) -> int | None:
    """The HTTP status behind a fetch failure, when there was one."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    match = re.match(r"\s*([1-5]\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _failure_outcome(status: int | None) -> str:
    """Anything unrecognised is transient, as in the acquisition policy: calling an outage an
    absence is a wrong and terminal statement about the paper."""
    if status in (404, 410):
        return NOT_FOUND
    if status in (401, 403, 407):
        return FORBIDDEN
    return TRANSIENT


async def _retrieve_bundle(url: str, fetcher, covered: list[str], ledger: list[dict]) -> tuple[dict | None, dict]:
    """Fetch and open the bundle, recording every attempt. Returns (members or None, last entry)."""
    entry: dict = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        at = _now_iso()
        outcome, status, error_class, contents = RETRIEVED, None, None, None
        try:
            blob = await fetcher(url)
        except Exception as exc:  # noqa: BLE001 - a fetch failure is a limitation of the run
            status = _status_of(exc)
            outcome, error_class = _failure_outcome(status), type(exc).__name__
            logger.info("supplementary bundle fetch failed for %s (attempt %d): %s", url, attempt, exc)
        else:
            if len(blob) > _MAX_BUNDLE_BYTES:
                outcome = TOO_LARGE
            else:
                try:
                    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                        contents = {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}
                except (zipfile.BadZipFile, OSError) as exc:
                    outcome, error_class = UNREADABLE, type(exc).__name__
                    logger.info("supplementary bundle at %s could not be read: %s", url, exc)
        entry = {
            "id": f"R{len(ledger) + 1}",
            "source": BUNDLE_SOURCE,
            "source_label": BUNDLE_SOURCE_LABEL,
            "url": url,
            "at": at,
            "attempt": attempt,
            "outcome": outcome,
            "http_status": status,
            "error_class": error_class,
            "artifacts": list(covered),
        }
        ledger.append(entry)
        if outcome == RETRIEVED:
            return contents, entry
        if outcome not in _RETRYABLE or attempt == MAX_ATTEMPTS:
            break
        await _sleep(_RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)])
    return None, entry


async def resolve_supplements(
    pmcid: str,
    references: list[dict],
    *,
    fetcher,
    thresholds: list[float] | None = None,
    ledger: list[dict] | None = None,
) -> list[dict]:
    """Resolve named references to real files and classify each by its content. Never raises.

    The prose says "Supplemental File S2" and the publisher deposits
    ``supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx``. Nothing connected the two, so
    the authors' entire analysis sat in a public bundle and the paper was reported as publishing no
    accessible code.

    change_7.3 section 1: **each attempt is recorded once, in ``ledger``, and every artifact points at
    the entry that decided it.** A failure used to be copied onto every row with ``setdefault``, so a
    later attempt could never replace the first reason and nothing recorded that an attempt had
    happened. A failed download leaves every artifact UNRESOLVED and its existence untouched: "we
    could not fetch it" is not "the authors did not publish it".
    """
    rows = establish_identity(references)
    if ledger is None:
        ledger = []
    covered = [r["identity"] for r in rows if r["kind"] not in (KIND_FIGURE, KIND_INDEX)]
    contents, entry = await _retrieve_bundle(_SUPPLEMENTARY_BUNDLE.format(pmcid=pmcid), fetcher, covered, ledger)

    if contents is None:
        for row in rows:
            if row.get("resolved"):
                continue  # retrieved by an earlier attempt; this failure does not undo that
            row["retrieval"] = {"status": RETRIEVAL_FAILED, "ledger": entry.get("id")}
            row.pop("failure_reason", None)  # a legacy copy; the ledger holds the one account now
        return rows

    claimed: set[str] = set()
    for row in rows:
        filename = _bundle_member_for(row, contents)
        if filename is None:
            if not row.get("resolved"):
                # The bundle arrived and this artifact is not in it. That alone is marked; the rest
                # resolve. Still not an absence: the manifest named it.
                row["retrieval"] = {"status": RETRIEVAL_NOT_IN_BUNDLE, "ledger": entry["id"]}
                row.pop("failure_reason", None)
            continue
        claimed.add(filename)
        row.pop("failure_reason", None)
        row.update(
            filename=filename,
            size_bytes=len(contents[filename]),
            resolved=True,
            retrieval={"status": RETRIEVAL_RETRIEVED, "ledger": entry["id"]},
        )
        if row["kind"] == KIND_REFERENCE:
            # A citation the manifest could not place, which the bundle's listing did.
            row["kind"] = KIND_ATTACHMENT
            row["identity"] = filename
            _add_reference(row, row["label"], IDENTIFIED_IN_BUNDLE)
        if row["kind"] == KIND_ATTACHMENT:
            row.update(
                role=classify_supplement(filename, contents[filename]),
                **measure_table(contents[filename], thresholds=thresholds),
            )

    # A file nobody cited is still part of what the paper published. The prose names three files;
    # the bundle holds seventeen, and the ones that are not figures can carry real inputs. The
    # figure images are the article's own figures and are kept apart from the attachments.
    for filename, blob_bytes in contents.items():
        if filename in claimed:
            continue
        kind = _bundle_kind(filename)
        row = _new_row(
            label=filename,
            filename=filename,
            mimetype=None,
            source=ATTACHED,
            kind=kind,
            identified_in=IDENTIFIED_IN_BUNDLE,
        )
        row.update(
            size_bytes=len(blob_bytes),
            resolved=True,
            retrieval={"status": RETRIEVAL_RETRIEVED, "ledger": entry["id"]},
        )
        if kind == KIND_ATTACHMENT:
            row.update(
                role=classify_supplement(filename, blob_bytes), **measure_table(blob_bytes, thresholds=thresholds)
            )
        rows.append(row)
    return rows


def _bundle_kind(filename: str) -> str:
    name = filename.lower()
    if name.endswith(_IMAGE_EXTENSIONS):
        return KIND_FIGURE
    if name.endswith((".html", ".htm")):
        return KIND_INDEX
    return KIND_ATTACHMENT


def _bundle_member_for(row: dict, contents: dict[str, bytes]) -> str | None:
    """The bundle member an artifact is, or None.

    A manifest attachment is matched by its own filename and nothing else. Fuzzy-matching one that
    is missing from the bundle is how the digits of a DOI in "Supplemental Material
    (supp_gr.252981.119_...)" became an identifier. Only a citation the manifest could not place is
    matched by its noun and number against the bundle's listing.
    """
    filename = row.get("filename")
    if filename:
        return filename if filename in contents else None
    candidates = [{"filename": name} for name in contents]
    for label in row.get("references") or [row.get("label")]:
        for citation in _citations(label or ""):
            match = _attachment_for(citation, candidates)
            if match is not None:
                return match["filename"]
    return None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# The digest is what a binding call sees of a supplement. Bounded ON PURPOSE: headers, counts and
# threshold splits are a few hundred bytes whatever the file holds, so a paper with a 20,000-row
# matrix costs the same as one with none. A number buried in row 5000 is not here, and the claim
# that needs it binds unresolved rather than wrong.
_MAX_DIGEST_COLUMNS = 12


def build_inventory_digest(supplements: list[dict]) -> str:
    """What the paper's supplements ARE, compactly enough to put in a prompt.

    change_7.1 section 3: the binding call was shown a metric name, a value and a unit, and asked
    which controlled metric the claim measures. For Groff that meant deciding what "54 samples"
    counts without the table saying 54 were collected and 51 analysed, and what "194 genes" counts
    without the results table where 88 of those rows clear the fold-change cutoff.

    An unresolved reference says so. "We have not fetched it" is not "the paper does not have it",
    and a model told the second would reason from an absence that is ours.
    """
    if not supplements:
        return ""

    lines: list[str] = []
    for row in supplements:
        if not isinstance(row, dict):
            continue
        label = row.get("label") or row.get("filename") or "supplement"
        if not row.get("resolved"):
            lines.append(f"- {label}: named by the paper; bioAF has not been retrieved it yet")
            continue

        parts = [f"role={row.get('role') or UNKNOWN_ROLE}"]
        if row.get("row_count") is not None:
            parts.append(f"{row['row_count']} rows")
        columns = [str(c) for c in (row.get("columns") or [])][:_MAX_DIGEST_COLUMNS]
        if columns:
            more = "" if len(row.get("columns") or []) <= _MAX_DIGEST_COLUMNS else ", ..."
            parts.append("columns: " + ", ".join(columns) + more)
        for name, count in (row.get("threshold_splits") or {}).items():
            parts.append(f"{count} rows with {name}")
        lines.append(f"- {label}: " + "; ".join(parts))

    return "The paper's supplements:\n" + "\n".join(lines)


# A column with few distinct values is a category (chromosome, sample type, call). One with a
# distinct value per row is an identifier, and counting it would be storing the table.
_MAX_CATEGORY_VALUES = 25


def measure_table(blob: bytes, *, thresholds: list[float] | None = None) -> dict:
    """What a tabular supplement holds, measured against what the paper claims. Never raises.

    change_7.1 section 7. The first pass hard-coded |log2FC| > 1 and > 2, which are Groff's cutoffs
    and therefore a paper-specific rule sitting in production code: a paper claiming a 1.5-fold
    cutoff got two numbers it never mentioned and not the one it did. The thresholds now come from
    the claims, so the table is measured for what was actually asserted about it.

    Groff also claims 146 sex-linked genes, which no fold-change threshold can produce: it needs
    the chromosome column read. Counting the values of any low-cardinality column is generic, works
    for sample types and morphokinetic calls just as well, and is what lets a claim about a
    column's contents be checked at all.

    **The rows themselves are never kept.** These summaries go into a prompt and a report; 194 rows
    of gene identifiers are cost, and "194 rows, 88 above the claimed cutoff" is the evidence.
    """
    text = _decode(blob)
    if text is None:
        return {}
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    delimiter = "\t" if "\t" in lines[0] else ","
    columns = [c.strip().strip('"') for c in lines[0].split(delimiter)]
    if len(columns) < 2:
        return {}

    rows = [line.split(delimiter) for line in lines[1:]]
    measured: dict = {"row_count": len(rows), "columns": columns}

    index = {c.lower(): i for i, c in enumerate(columns)}
    fold_change = next((index[c] for c in ("log2foldchange", "log2fc", "logfc") if c in index), None)
    if fold_change is not None and thresholds:
        splits: dict[str, int] = {}
        for cutoff in thresholds:
            count = 0
            for cells in rows:
                if fold_change >= len(cells):
                    continue
                try:
                    if abs(float(cells[fold_change].strip().strip('"'))) > cutoff:
                        count += 1
                except ValueError:
                    continue
            splits[f"abs_log2fc>{cutoff:g}"] = count
        measured["threshold_splits"] = splits

    categories = {}
    for position, name in enumerate(columns):
        values = [c[position].strip().strip('"') for c in rows if position < len(c)]
        distinct = {v for v in values if v}
        # All-distinct means an identifier column, whatever the table's size. Counting one stores
        # the identifiers, which is the table by another name.
        if not distinct or len(distinct) > _MAX_CATEGORY_VALUES:
            continue
        if len(values) > 1 and len(distinct) == len(values):
            continue
        if all(_is_number(v) for v in distinct):
            continue
        categories[name] = {value: values.count(value) for value in sorted(distinct)}
    if categories:
        measured["category_counts"] = categories
    return measured


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def merge_resource_identity(supplements: list[dict] | None) -> list[dict]:
    """One file, one row, however many ways the paper referred to it.

    change_7.1 section 7. The deployed rerun listed Supplemental Files S1, S2 and S3 twice each:
    once as the JATS media element that carries the filename, once as the prose reference that
    carries the identifier a reader recognises. Same bytes, two rows, and a reader counting the
    paper's attachments gets the wrong number.

    **Only RESOLVED rows merge.** Two references bioAF could not resolve are not known to be the
    same file, and merging them would invent a fact rather than remove a duplicate.

    The reader-facing identifier wins the label: "Supplemental File S1" is what the paper's prose
    cites, and "Supplemental Material (s1.txt)" is the publisher's packaging. Every reference is
    kept so a search for either one still finds the resource.
    """
    merged: dict[str, dict] = {}
    unresolved: list[dict] = []

    for row in supplements or []:
        if not isinstance(row, dict):
            continue
        filename = row.get("filename")
        if not row.get("resolved") or not filename:
            unresolved.append({**row, "references": [row.get("label")] if row.get("label") else []})
            continue

        existing = merged.get(filename)
        if existing is None:
            merged[filename] = {**row, "references": [row["label"]] if row.get("label") else []}
            continue

        # Later rows fill gaps rather than overwrite: a measurement taken once is not re-taken, and
        # an established role must never be reset to "unknown" by a duplicate that carried none.
        for key, value in row.items():
            if key in ("label", "references"):
                continue
            if value in (None, {}, []) or (key == "role" and value == UNKNOWN_ROLE):
                continue
            if existing.get(key) in (None, {}, [], UNKNOWN_ROLE):
                existing[key] = value
        if row.get("label") and row["label"] not in existing["references"]:
            existing["references"].append(row["label"])
        existing["label"] = _preferred_label(existing["references"])

    return list(merged.values()) + unresolved


def _preferred_label(references: list[str]) -> str:
    """The name a reader would look for.

    A prose citation ("Supplemental File S2") names the artifact the way the paper does. A manifest
    label with the filename in parentheses is packaging. Prefer the first, shortest citation.
    """
    cited = [r for r in references if r and _NAMED_SUPPLEMENT_RE.search(r)]
    if cited:
        return min(cited, key=len)
    return references[0] if references else "supplement"


def apply_retrieval_to_code_sources(sources: list[dict] | None, supplements: list[dict] | None) -> list[dict]:
    """Carry what retrieval established back onto the paper's code rows.

    change_7.1 section 7. Study 32 recorded Supplemental File S2 as ``exists: yes, accessible:
    not_attempted`` in the same evidence bundle that had downloaded it, read its R Markdown and
    classified it as code. An artifact cannot be retrieved and not-attempted at once.

    **Retrieval, extraction and execution are separate statuses.** A DOCX that downloaded but could
    not be read is accessible and unextracted, and collapsing the two would claim we had the code.
    """
    by_reference: dict[str, dict] = {}
    for row in supplements or []:
        if not isinstance(row, dict):
            continue
        for reference in [row.get("label"), *(row.get("references") or [])]:
            if reference:
                by_reference[str(reference).strip().lower()] = row

    updated: list[dict] = []
    for source in sources or []:
        row = dict(source)
        key = str(row.get("identifier") or row.get("url") or "").strip().lower()
        supplement = by_reference.get(key)
        if supplement and supplement.get("resolved"):
            row["accessible"] = YES_ANSWER
            row["accessible_reason"] = f"retrieved as {supplement.get('filename')}"
            row["code_extracted"] = supplement.get("role") == CODE
        else:
            row.setdefault("code_extracted", False)
        updated.append(row)
    return updated
