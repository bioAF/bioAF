"""bioAF can only align against four assemblies, and nothing says so out loud.

`_normalize_reference_genome` returns None for anything outside human and mouse, the launch then
takes the pipeline's seeded default, and a non-human study aligns against the wrong genome and
completes green. That caps every non-human, non-mouse paper at Layer 0 (routed but never run), which
is most of agricultural, plant, model-organism and microbiology literature.

Three tables have to agree for an assembly to work, and they live in three files on purpose:

  * the extractor's aliases, which read messy prose out of a methods section;
  * the launch's aliases and reference URLs, which decide what a run actually aligns against;
  * the seeded `reference_genome` controlled vocabulary, which 422s a launch that names anything else.

These tests are what holds them together. Every URL in the table was fetch-verified (HTTP 206 on a
range request) rather than pattern-matched, because Ensembl's layout is only mostly regular: rat,
C. elegans and Drosophila publish no `primary_assembly` file at all, and Arabidopsis is not on the
main FTP site.
"""

import importlib.util
import pathlib

import pytest

from app.services.pipeline_run_service import _ASSEMBLY_ALIASES, _ENSEMBL_REFERENCE_BY_GENOME
from app.services.validation_extraction_service import _normalize_reference_genome

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _seeded_reference_genomes() -> set[str]:
    """Every `reference_genome` value an instance's controlled vocabulary is seeded with.

    Read out of the migrations themselves rather than a hand-kept copy: seeded rows are create-once,
    so a token added to the code tables and not to a migration works in tests, works on a fresh
    install, and 422s on every instance that already exists.
    """
    seeded: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_vocab_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ("SEED_DATA", "REFERENCE_GENOMES"):
            rows = getattr(module, name, None)
            for row in rows or []:
                if row[0] == "reference_genome":
                    seeded.add(row[1])
    return seeded


@pytest.mark.parametrize(
    "prose,expected",
    [
        ("GRCz11", "GRCz11"),
        ("danRer11", "GRCz11"),
        ("zebrafish GRCz11 / Ensembl 110", "GRCz11"),
        ("mRatBN7.2", "mRatBN7.2"),
        ("rn7", "mRatBN7.2"),
        ("WBcel235", "WBcel235"),
        ("ce11", "WBcel235"),
        ("BDGP6.32", "BDGP6"),
        ("dm6", "BDGP6"),
        ("TAIR10", "TAIR10"),
        ("Araport11 on TAIR10", "TAIR10"),
    ],
)
def test_the_names_papers_actually_use_normalize(prose, expected):
    assert _normalize_reference_genome(prose) == expected


def test_the_assemblies_bioaf_already_carried_still_normalize():
    """The four that worked before must keep working, spelled either way."""
    for prose, expected in (
        ("GRCh38", "GRCh38"),
        ("hg38", "GRCh38"),
        ("hg19", "GRCh37"),
        ("mm10", "GRCm38"),
        ("GRCm39", "GRCm39"),
    ):
        assert _normalize_reference_genome(prose) == expected


def test_an_older_assembly_of_a_new_organism_is_not_silently_upgraded():
    """Zv9 is not GRCz11 and Rnor_6.0 is not mRatBN7.2. Answering with the current assembly would
    align against a genome the paper never used and report the difference as biology."""
    for prose in ("Zv9", "danRer7", "Rnor_6.0", "rn6", "ce10", "dm3", "TAIR9"):
        assert _normalize_reference_genome(prose) is None, prose


def test_every_assembly_the_extractor_can_emit_is_launchable():
    """The gap this closes. A token the extractor produces that the launch cannot resolve leaves the
    run taking the pipeline's seeded default, which is the silent wrong-genome alignment."""
    for prose, token in (
        ("GRCh38", "GRCh38"),
        ("GRCh37", "GRCh37"),
        ("GRCm39", "GRCm39"),
        ("GRCm38", "GRCm38"),
        ("GRCz11", "GRCz11"),
        ("mRatBN7.2", "mRatBN7.2"),
        ("WBcel235", "WBcel235"),
        ("BDGP6", "BDGP6"),
        ("TAIR10", "TAIR10"),
    ):
        assert _normalize_reference_genome(prose) == token
        assert _ASSEMBLY_ALIASES.get(token.lower()) == token
        assert token in _ENSEMBL_REFERENCE_BY_GENOME, token


def test_t2t_chm13_is_still_deliberately_unlaunchable():
    """Recognized as an assembly so a pinned pipeline REFUSES rather than quietly aligning against
    whatever it was seeded with, and carried by no reference, so it cannot run."""
    assert _normalize_reference_genome("T2T-CHM13") == "T2T-CHM13"
    assert _ASSEMBLY_ALIASES.get("chm13") == "T2T-CHM13"
    assert "T2T-CHM13" not in _ENSEMBL_REFERENCE_BY_GENOME


def test_every_reference_url_names_its_own_assembly_and_species():
    """The copy-paste this catches is a new organism pointed at the previous one's files, which
    aligns cleanly and answers about the wrong species."""
    species = {
        "GRCh38": "homo_sapiens",
        "GRCh37": "homo_sapiens",
        "GRCm39": "mus_musculus",
        "GRCm38": "mus_musculus",
        "GRCz11": "danio_rerio",
        "mRatBN7.2": "rattus_norvegicus",
        "WBcel235": "caenorhabditis_elegans",
        "BDGP6": "drosophila_melanogaster",
        "TAIR10": "arabidopsis_thaliana",
    }
    for assembly, (fasta, gtf) in _ENSEMBL_REFERENCE_BY_GENOME.items():
        for url in (fasta, gtf):
            assert species[assembly] in url.lower(), (assembly, url)
            assert url.endswith(".gz"), (assembly, url)
        assert ".dna." in fasta and "/fasta/" in fasta, assembly
        assert "/gtf/" in gtf, assembly


def test_every_launchable_assembly_is_in_the_seeded_vocabulary():
    """`launch_run` validates `reference_genome` against the seeded controlled vocabulary and 422s
    on anything else, so widening the code tables without a migration would refuse the very studies
    it was meant to unblock."""
    seeded = _seeded_reference_genomes()
    assert seeded, "no reference_genome vocabulary found in the migrations"
    missing = sorted(set(_ENSEMBL_REFERENCE_BY_GENOME) - seeded)
    assert not missing, missing
