import csv
import io
import logging
import re

logger = logging.getLogger("bioaf.sample_sheet")

_ILLUMINA_READ_RE = re.compile(r"_(R[12]|I[12])_")

# A sample name that is purely numeric (int or float). nf-core/nf-schema infers a
# CSV column's type from its values, so such a name is typed as integer/number and
# rejected against the schema's string 'sample' field ("Value is [integer] but
# should be [string]"). We prefix these so the value is unambiguously a string.
_NUMERIC_NAME_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _safe_sample_name(sample) -> str:
    """Resolve a sample's 'sample' column value, guaranteed to be string-typed.

    Uses the sample's ``external_id`` (falling back to ``sample_<id>`` when it is
    empty), then prefixes any purely numeric name so nf-schema does not coerce it
    to an integer and reject it. Non-numeric names and the fallback pass through
    unchanged, so this only changes names that nf-core would reject anyway.
    """
    name = (getattr(sample, "external_id", None) or "").strip()
    if not name:
        return f"sample_{sample.id}"
    if _NUMERIC_NAME_RE.fullmatch(name):
        return f"sample_{name}"
    return name


def _get_read_type(f) -> str | None:
    """Return read type (R1, R2, I1, I2) from tags_json or filename pattern."""
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("read:"):
            return tag.split(":", 1)[1]
    # Fallback to filename pattern
    filename = getattr(f, "filename", "") or ""
    m = _ILLUMINA_READ_RE.search(filename)
    if m:
        return m.group(1)
    return None


def _get_lane(f) -> str:
    """Return lane identifier from tags_json or filename, default '000'."""
    tags = getattr(f, "tags_json", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("lane:"):
            return tag.split(":", 1)[1]
    filename = getattr(f, "filename", "") or ""
    m = re.search(r"_L(\d{3})_", filename)
    if m:
        return m.group(1)
    return "000"


def _extract_fastq_lane_pairs(sample) -> list[tuple[str, str]]:
    """Extract (fastq_1, fastq_2) pairs grouped by lane.

    Excludes index reads (I1, I2). Uses tags_json read type when available,
    falls back to Illumina filename convention (_R1_/_R2_).
    Returns one tuple per lane, sorted by lane number.
    """
    # Prefer the input-eligible set the launcher resolves (raw inputs only, with
    # prior pipeline/notebook outputs excluded by default). Fall back to all
    # linked files for callers that don't set it. isinstance guards against a
    # MagicMock auto-vivifying the attribute in unit tests.
    files = getattr(sample, "_input_files", None)
    if not isinstance(files, list):
        files = getattr(sample, "files", None) or []
    fastq_files = [f for f in files if getattr(f, "storage_uri", None)]
    if not fastq_files:
        return [("", "")]

    # Classify each file by read type
    lanes: dict[str, dict[str, str]] = {}
    unclassified = []
    for f in fastq_files:
        read_type = _get_read_type(f)
        if read_type and read_type.startswith("I"):
            continue  # Skip index reads
        if read_type in ("R1", "R2"):
            lane = _get_lane(f)
            lanes.setdefault(lane, {})
            lanes[lane][read_type] = f.storage_uri
        else:
            unclassified.append(f)

    if lanes:
        result = []
        for lane_key in sorted(lanes):
            r1 = lanes[lane_key].get("R1", "")
            r2 = lanes[lane_key].get("R2", "")
            result.append((r1, r2))
        return result

    # Fallback for files without read type info: sort by filename
    unclassified.sort(key=lambda f: getattr(f, "filename", "") or getattr(f, "storage_uri", ""))
    fastq_1 = unclassified[0].storage_uri if len(unclassified) > 0 else ""
    fastq_2 = unclassified[1].storage_uri if len(unclassified) > 1 else ""
    return [(fastq_1, fastq_2)]


def _extract_fastq_paths(sample) -> tuple[str, str]:
    """Extract fastq_1 and fastq_2 GCS URIs from sample.files.

    Uses read type metadata to correctly identify R1/R2 and exclude index reads.
    For single-lane data returns one pair; for multi-lane see _extract_fastq_lane_pairs.
    """
    pairs = _extract_fastq_lane_pairs(sample)
    return pairs[0] if pairs else ("", "")


class SampleSheetService:
    @staticmethod
    def generate_scrnaseq_sheet(samples: list, parameters: dict) -> str:
        """Generate nf-core/scrnaseq sample sheet CSV.

        If samples don't have linked files, falls back to input_paths in parameters.
        """
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "expected_cells"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            expected_cells = parameters.get("expected_cells", 10000)
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2, expected_cells])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2, expected_cells])

        return output.getvalue()

    @staticmethod
    def generate_rnaseq_sheet(samples: list, parameters: dict) -> str:
        """Generate nf-core/rnaseq sample sheet CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2", "strandedness"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            strandedness = parameters.get("strandedness", "auto")
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2, strandedness])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2, strandedness])

        return output.getvalue()

    @staticmethod
    def generate_generic_sheet(samples: list, parameters: dict) -> str:
        """Generic fallback CSV sample sheet."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["sample", "fastq_1", "fastq_2"])

        input_paths = parameters.get("input_paths", {})

        for sample in samples:
            sample_name = _safe_sample_name(sample)
            paths = input_paths.get(str(sample.id), [])
            if paths:
                fastq_1 = paths[0] if len(paths) > 0 else ""
                fastq_2 = paths[1] if len(paths) > 1 else ""
                writer.writerow([sample_name, fastq_1, fastq_2])
            else:
                for fastq_1, fastq_2 in _extract_fastq_lane_pairs(sample):
                    writer.writerow([sample_name, fastq_1, fastq_2])

        return output.getvalue()

    @staticmethod
    def generate_fetchngs_ids(parameters: dict) -> str:
        """Build nf-core/fetchngs's --input ids file: one database accession per line, no header.

        fetchngs pulls FASTQ + metadata from these accessions itself (no per-sample files). bioAF
        carries them in parameters["accessions"] (a list, or a comma/space/newline-separated string);
        the run path feeds this file in via --input.
        """
        raw = parameters.get("accessions") or []
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw)
        ids = [str(a).strip() for a in raw if str(a).strip()]
        return "\n".join(ids) + ("\n" if ids else "")

    @staticmethod
    def generate_sheet(pipeline_key: str, samples: list, parameters: dict) -> str:
        """Route to the correct sheet generator based on pipeline type."""
        if "scrnaseq" in pipeline_key:
            return SampleSheetService.generate_scrnaseq_sheet(samples, parameters)
        elif "rnaseq" in pipeline_key:
            return SampleSheetService.generate_rnaseq_sheet(samples, parameters)
        elif "fetchngs" in pipeline_key:
            return SampleSheetService.generate_fetchngs_ids(parameters)
        else:
            return SampleSheetService.generate_generic_sheet(samples, parameters)
