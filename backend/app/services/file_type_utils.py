"""Shared file type detection and artifact classification utilities."""

from pathlib import PurePosixPath

# File type mapping by extension
FILE_TYPE_MAP: dict[str, str] = {
    ".fastq": "fastq",
    ".fq": "fastq",
    ".bam": "bam",
    ".sam": "bam",
    ".cram": "bam",
    ".h5ad": "h5ad",
    ".h5": "h5",
    ".csv": "count_matrix",
    ".tsv": "count_matrix",
    ".mtx": "count_matrix",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".txt": "document",
    ".md": "document",
    ".html": "report",
}

# Extensions that imply compression - check inner extension too
COMPRESSED_EXTS: set[str] = {".gz", ".bz2", ".xz", ".zip"}

# Artifact type mapping - semantic categories for pipeline outputs
ARTIFACT_TYPE_MAP: dict[str, str] = {
    ".bam": "alignment",
    ".sam": "alignment",
    ".cram": "alignment",
    ".h5ad": "anndata",
    ".h5": "feature_matrix",
    ".csv": "count_matrix",
    ".tsv": "count_matrix",
    ".mtx": "count_matrix",
    ".fastq": "fastq",
    ".fq": "fastq",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".pdf": "document",
    ".html": "report",
}


def detect_file_type(filename: str) -> str:
    """Map filename extension to file type category."""
    p = PurePosixPath(filename)
    ext = p.suffix.lower()

    # Check for compressed double extensions like .fastq.gz
    if ext in COMPRESSED_EXTS:
        inner_ext = PurePosixPath(p.stem).suffix.lower()
        if inner_ext in FILE_TYPE_MAP:
            return FILE_TYPE_MAP[inner_ext]

    return FILE_TYPE_MAP.get(ext, "other")


def classify_artifact_type(filename: str) -> str:
    """Map filename to a semantic artifact category for pipeline outputs."""
    p = PurePosixPath(filename)
    ext = p.suffix.lower()

    if ext in COMPRESSED_EXTS:
        inner_ext = PurePosixPath(p.stem).suffix.lower()
        if inner_ext in ARTIFACT_TYPE_MAP:
            return ARTIFACT_TYPE_MAP[inner_ext]

    return ARTIFACT_TYPE_MAP.get(ext, "other")
