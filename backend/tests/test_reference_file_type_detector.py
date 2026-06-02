"""TDD: detect_reference_file_type maps reference-data filenames to a
friendly display label for the Type column on the reference detail page.

Distinct from detect_file_type (which returns semantic categories like
'fastq' / 'bam' / 'count_matrix' / 'other' for the ingest + pipeline
codepaths). The reference version returns user-readable labels like
'FASTA' or 'STAR genome' and returns None for unknowns so the UI can
render an em-dash rather than misleading the operator with 'other'."""

from app.services.file_type_utils import detect_reference_file_type


class TestCommonBioinformaticsExtensions:
    def test_fasta_variants(self):
        assert detect_reference_file_type("genome.fa") == "FASTA"
        assert detect_reference_file_type("genome.fasta") == "FASTA"
        assert detect_reference_file_type("proteins.faa") == "FASTA (protein)"
        assert detect_reference_file_type("nucleotides.fna") == "FASTA (nucleotide)"

    def test_fasta_index(self):
        assert detect_reference_file_type("genome.fa.fai") == "FASTA index"
        assert detect_reference_file_type("genome.fasta.fai") == "FASTA index"

    def test_gtf_gff(self):
        assert detect_reference_file_type("genes.gtf") == "GTF"
        assert detect_reference_file_type("genes.gff") == "GFF"
        assert detect_reference_file_type("genes.gff3") == "GFF3"
        assert detect_reference_file_type("regions.bed") == "BED"

    def test_alignment_and_variant(self):
        assert detect_reference_file_type("aligned.bam") == "BAM"
        assert detect_reference_file_type("aligned.bam.bai") == "BAM index"
        assert detect_reference_file_type("variants.vcf") == "VCF"
        assert detect_reference_file_type("variants.vcf.gz") == "VCF (gzipped)"
        assert detect_reference_file_type("variants.vcf.gz.tbi") == "Tabix index"

    def test_single_cell_formats(self):
        assert detect_reference_file_type("atlas.h5ad") == "AnnData"
        assert detect_reference_file_type("atlas.loom") == "Loom"
        assert detect_reference_file_type("matrix.mtx") == "Matrix Market"

    def test_serialized_data(self):
        assert detect_reference_file_type("genes.pickle") == "Pickle"
        assert detect_reference_file_type("genes.pkl") == "Pickle"
        assert detect_reference_file_type("reference.json") == "JSON"
        assert detect_reference_file_type("config.yaml") == "YAML"
        assert detect_reference_file_type("counts.tsv") == "TSV"
        assert detect_reference_file_type("counts.csv") == "CSV"
        assert detect_reference_file_type("data.h5") == "HDF5"
        assert detect_reference_file_type("data.hdf5") == "HDF5"

    def test_archives(self):
        assert detect_reference_file_type("bundle.tar.gz") == "tar.gz archive"
        assert detect_reference_file_type("bundle.tgz") == "tar.gz archive"
        assert detect_reference_file_type("bundle.tar") == "tar archive"
        assert detect_reference_file_type("plain.gz") == "gzip"


class TestStarGenomeIndexFiles:
    """STAR's --genomeDir layout has well-known extension-less filenames
    (Genome, SA, SAindex, etc.) that show up in basically every 10x and
    Cell Ranger reference. Detect them by name + directory hint so the
    Type column is useful instead of empty."""

    def test_star_files_with_directory_hint(self):
        assert detect_reference_file_type("refdata/star/Genome") == "STAR genome"
        assert detect_reference_file_type("refdata/star/SA") == "STAR suffix array"
        assert detect_reference_file_type("refdata/star/SAindex") == "STAR SA index"
        assert detect_reference_file_type("refdata/star/chrLength.txt") == "STAR chrom lengths"
        assert detect_reference_file_type("refdata/star/chrName.txt") == "STAR chrom names"
        assert detect_reference_file_type("refdata/star/chrNameLength.txt") == "STAR chrom name+length"
        assert detect_reference_file_type("refdata/star/chrStart.txt") == "STAR chrom starts"
        assert detect_reference_file_type("refdata/star/exonGeTrInfo.tab") == "STAR exon-gene-transcript info"
        assert detect_reference_file_type("refdata/star/geneInfo.tab") == "STAR gene info"
        assert detect_reference_file_type("refdata/star/genomeParameters.txt") == "STAR genome parameters"

    def test_unknown_extensionless_returns_none(self):
        assert detect_reference_file_type("refdata/random/UNKNOWN") is None


class TestFallbacks:
    def test_unknown_extension_returns_none(self):
        """Unrecognized extension -> None so the UI shows an em-dash
        instead of a misleading guess."""
        assert detect_reference_file_type("data.xyz") is None
        assert detect_reference_file_type("noext") is None

    def test_path_is_basename_aware(self):
        """Directories should be stripped before matching the extension."""
        assert detect_reference_file_type("a/b/c/genome.fa") == "FASTA"
