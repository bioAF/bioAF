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


# ---------------------------------------------------------------------------
# Reference-data display labels.
#
# Distinct from detect_file_type / classify_artifact_type above, which return
# semantic categories ('fastq', 'bam', 'count_matrix', 'other') for pipeline
# and ingest classification. The reference-data flavor returns human-readable
# labels for the Type column on the reference detail page (e.g., 'FASTA',
# 'STAR genome', 'Tabix index'). Returns None for unknowns so the UI renders
# an em-dash rather than misleading the operator with 'other'.
# ---------------------------------------------------------------------------


# Compound suffixes are checked first so 'foo.tar.gz' resolves to 'tar.gz
# archive' instead of bare 'gzip', and 'foo.vcf.gz' to 'VCF (gzipped)'.
_REFERENCE_COMPOUND_SUFFIXES: list[tuple[str, str]] = [
    (".tar.gz", "tar.gz archive"),
    (".tgz", "tar.gz archive"),
    (".fastq.gz", "FASTQ (gzipped)"),
    (".fq.gz", "FASTQ (gzipped)"),
    (".fasta.gz", "FASTA (gzipped)"),
    (".fa.gz", "FASTA (gzipped)"),
    (".gtf.gz", "GTF (gzipped)"),
    (".gff.gz", "GFF (gzipped)"),
    (".gff3.gz", "GFF3 (gzipped)"),
    (".bed.gz", "BED (gzipped)"),
    (".vcf.gz", "VCF (gzipped)"),
    (".vcf.gz.tbi", "Tabix index"),
    (".fa.fai", "FASTA index"),
    (".fasta.fai", "FASTA index"),
    (".bam.bai", "BAM index"),
    (".cram.crai", "CRAM index"),
]


_REFERENCE_EXT_MAP: dict[str, str] = {
    # Sequences
    ".fa": "FASTA",
    ".fasta": "FASTA",
    ".fna": "FASTA (nucleotide)",
    ".faa": "FASTA (protein)",
    ".fai": "FASTA index",
    ".fastq": "FASTQ",
    ".fq": "FASTQ",
    # Annotations
    ".gtf": "GTF",
    ".gff": "GFF",
    ".gff3": "GFF3",
    ".bed": "BED",
    # Alignment
    ".sam": "SAM",
    ".bam": "BAM",
    ".bai": "BAM index",
    ".cram": "CRAM",
    ".crai": "CRAM index",
    # Variant
    ".vcf": "VCF",
    ".bcf": "BCF",
    ".tbi": "Tabix index",
    ".csi": "CSI index",
    # Single-cell / matrix
    ".h5ad": "AnnData",
    ".loom": "Loom",
    ".h5": "HDF5",
    ".hdf5": "HDF5",
    ".mtx": "Matrix Market",
    # Generic data
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".tsv": "TSV",
    ".csv": "CSV",
    ".tab": "TAB",
    ".txt": "Text",
    # Python / R / NumPy
    ".pickle": "Pickle",
    ".pkl": "Pickle",
    ".npy": "NumPy array",
    ".npz": "NumPy archive",
    ".rds": "R Data",
    ".rdata": "R Data",
    # Archives
    ".tar": "tar archive",
    ".gz": "gzip",
    ".zip": "zip",
    ".bz2": "bzip2",
    ".xz": "xz",
}


# STAR's --genomeDir layout has well-known extension-less filenames that show
# up in 10x and Cell Ranger references. Match by basename, lowercased.
_STAR_INDEX_FILES: dict[str, str] = {
    "genome": "STAR genome",
    "sa": "STAR suffix array",
    "saindex": "STAR SA index",
    "sjdbinfo.txt": "STAR splice junction info",
    "sjdblist.out.tab": "STAR splice junction list",
    "transcriptinfo.tab": "STAR transcript info",
    "exoninfo.tab": "STAR exon info",
    "exongetrinfo.tab": "STAR exon-gene-transcript info",
    "genedict": "STAR gene dict",
    "geneinfo.tab": "STAR gene info",
    "chrnamelength.txt": "STAR chrom name+length",
    "chrname.txt": "STAR chrom names",
    "chrlength.txt": "STAR chrom lengths",
    "chrstart.txt": "STAR chrom starts",
    "genomeparameters.txt": "STAR genome parameters",
}


def detect_reference_file_type(filename: str) -> str | None:
    """Return a friendly display label for a reference-data filename.

    Covers the bioinformatics formats commonly found in reference bundles
    (FASTA, GTF, VCF, BAM, AnnData, Loom, Matrix Market, ...) plus STAR's
    extension-less genome-index files. Returns None for unknowns so the
    Type column can render an em-dash instead of a misleading guess.
    """
    if not filename:
        return None
    base = filename.rsplit("/", 1)[-1].lower()

    # Longest compound suffix wins.
    for suffix, label in sorted(_REFERENCE_COMPOUND_SUFFIXES, key=lambda s: -len(s[0])):
        if base.endswith(suffix):
            return label

    # STAR-specific extensionless files (Genome, SA, SAindex, ...).
    if base in _STAR_INDEX_FILES:
        return _STAR_INDEX_FILES[base]

    # Trailing simple extension.
    if "." in base:
        ext = "." + base.rsplit(".", 1)[-1]
        if ext in _REFERENCE_EXT_MAP:
            return _REFERENCE_EXT_MAP[ext]

    return None


def classify_artifact_type(filename: str) -> str:
    """Map filename to a semantic artifact category for pipeline outputs."""
    p = PurePosixPath(filename)
    ext = p.suffix.lower()

    if ext in COMPRESSED_EXTS:
        inner_ext = PurePosixPath(p.stem).suffix.lower()
        if inner_ext in ARTIFACT_TYPE_MAP:
            return ARTIFACT_TYPE_MAP[inner_ext]

    return ARTIFACT_TYPE_MAP.get(ext, "other")
