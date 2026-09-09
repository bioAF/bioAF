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

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile

logger = logging.getLogger("bioaf.supplement_inventory")

# Where a row came from, which is also how far discovery got with it.
ATTACHED = "attached"  # a media element in the JATS, with a filename
NAMED_IN_TEXT = "named_in_text"  # the prose cites it; nothing has resolved it to a file yet

# What a resource IS, established by looking inside it.
SAMPLE_METADATA = "sample_metadata"
EXPRESSION_MATRIX = "expression_matrix"
RESULTS_TABLE = "results_table"
CODE = "code"
SUPPORTING_INPUT = "supporting_input"
UNKNOWN_ROLE = "unknown"

_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# "Supplemental File S2", "Supplementary Table S1", "Supplemental Data Set S3". Journals differ on
# every word except the pattern.
_NAMED_SUPPLEMENT_RE = re.compile(
    r"\bSupplement(?:al|ary)\s+(?:File|Table|Data\s*Set|Dataset|Material)s?\s+(S\d+[A-Za-z]?)", re.I
)

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
            rows.setdefault(
                href,
                {
                    "label": label if label not in taken else f"{label} ({href})",
                    "filename": href,
                    "mimetype": _mimetype_of(media),
                    "source": ATTACHED,
                    "role": UNKNOWN_ROLE,
                    "size_bytes": None,
                    "resolved": False,
                },
            )

    body_text = " ".join(t for t in root.itertext() if t)
    for match in _NAMED_SUPPLEMENT_RE.finditer(body_text):
        # Normalised so "Supplementary File S2" and "Supplemental file s2" are one row, not three.
        identifier = match.group(1).upper()
        label = f"Supplemental File {identifier}"
        rows.setdefault(
            label,
            {
                "label": label,
                "filename": None,
                "mimetype": None,
                "source": NAMED_IN_TEXT,
                "role": UNKNOWN_ROLE,
                "size_bytes": None,
                "resolved": False,
            },
        )

    return list(rows.values())


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
    if sum(1 for c in columns if c in _RESULT_COLUMNS) >= 2:
        # A results table, and specifically NOT an expression matrix: these are one row per gene of
        # an analysis that already ran, not the per-sample values needed to run it again.
        return RESULTS_TABLE

    if any(any(m in c for m in _METADATA_COLUMNS) for c in columns):
        return SAMPLE_METADATA

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

_IDENTIFIER_RE = re.compile(r"\bS?(\d+)\b")


async def resolve_supplements(pmcid: str, references: list[dict], *, fetcher) -> list[dict]:
    """Resolve named references to real files and classify each by its content. Never raises.

    The prose says "Supplemental File S2" and the publisher deposits
    ``supp_gr.252981.119_Supplemental_File_2_AllRCode_Review.docx``. Nothing connected the two, so
    the authors' entire analysis sat in a public bundle and the paper was reported as publishing no
    accessible code.

    A download failure leaves every reference UNRESOLVED with a reason. "We could not fetch it" and
    "the authors did not publish it" are different statements and only one is a finding.
    """
    rows = [dict(r) for r in references or []]

    try:
        blob = await fetcher(_SUPPLEMENTARY_BUNDLE.format(pmcid=pmcid))
    except Exception as exc:  # noqa: BLE001 - a fetch failure is a limitation of the run
        logger.info("supplementary bundle fetch failed for %s: %s", pmcid, exc)
        for row in rows:
            row.setdefault("failure_reason", f"bioAF could not download this paper's supplementary bundle ({exc})")
        return rows

    if len(blob) > _MAX_BUNDLE_BYTES:
        for row in rows:
            row.setdefault("failure_reason", "this paper's supplementary bundle is larger than bioAF will download")
        return rows

    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            contents = {info.filename: zf.read(info.filename) for info in zf.infolist() if not info.is_dir()}
    except (zipfile.BadZipFile, OSError) as exc:
        logger.info("supplementary bundle for %s could not be read: %s", pmcid, exc)
        for row in rows:
            row.setdefault("failure_reason", "this paper's supplementary bundle could not be read")
        return rows

    claimed: set[str] = set()
    for row in rows:
        filename = row.get("filename") if row.get("filename") in contents else _match_filename(row, contents)
        if filename is None:
            row["failure_reason"] = "bioAF could not find a file in the bundle matching this reference"
            continue
        claimed.add(filename)
        row.update(
            filename=filename,
            size_bytes=len(contents[filename]),
            role=classify_supplement(filename, contents[filename]),
            resolved=True,
            failure_reason=None,
            **_measurements(contents[filename]),
        )

    # A file nobody cited is still part of what the paper published. The prose names three files;
    # the bundle holds seventeen, and the ones that are not figures can carry real inputs.
    for filename, blob_bytes in contents.items():
        if filename in claimed:
            continue
        rows.append(
            {
                "label": filename,
                "filename": filename,
                "mimetype": None,
                "source": ATTACHED,
                "role": classify_supplement(filename, blob_bytes),
                "size_bytes": len(blob_bytes),
                "resolved": True,
                "failure_reason": None,
                **_measurements(blob_bytes),
            }
        )
    return rows


def _match_filename(row: dict, contents: dict[str, bytes]) -> str | None:
    """The bundle file a named reference points at, by its identifier.

    Publishers mangle names in every direction, but the identifier survives: "Supplemental File S2"
    becomes ``..._Supplemental_File_2_...``. Matching on the number inside a supplement-ish filename
    is what connects them; matching on the label as a whole never would.
    """
    match = _IDENTIFIER_RE.search(row.get("label") or "")
    if match is None:
        return None
    wanted = match.group(1)
    for filename in contents:
        normalized = re.sub(r"[^a-z0-9]+", "_", filename.lower())
        if "supplement" not in normalized:
            continue
        if re.search(rf"(?:file|table|data|dataset|material)s?_{wanted}(?:_|\b)", normalized):
            return filename
    return None


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


def _measurements(blob: bytes) -> dict:
    """Row count, column names and threshold splits for a tabular supplement. Never raises.

    **The rows themselves are never kept.** These numbers go into a prompt, where 194 rows of gene
    identifiers are cost and "194 rows, 88 of them above |log2FC| > 2" is the evidence. Computing
    the splits here is what lets a claim be checked against the number it actually refers to: the
    paper states both 194 and 88 in one sentence, and they are different claims.
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

    measured: dict = {"row_count": len(lines) - 1, "columns": columns}

    index = {c.lower(): i for i, c in enumerate(columns)}
    fold_change = next((index[c] for c in ("log2foldchange", "log2fc", "logfc") if c in index), None)
    if fold_change is not None:
        splits: dict[str, int] = {}
        for cutoff in (1.0, 2.0):
            count = 0
            for line in lines[1:]:
                cells = line.split(delimiter)
                if fold_change >= len(cells):
                    continue
                try:
                    if abs(float(cells[fold_change].strip().strip('"'))) > cutoff:
                        count += 1
                except ValueError:
                    continue
            splits[f"abs_log2fc>{cutoff:g}"] = count
        measured["threshold_splits"] = splits
    return measured
