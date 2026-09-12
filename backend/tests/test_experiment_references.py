"""change_7.5 section 2.3: each experiment's reference, stated as two parts and never defaulted.

Study 38's paper aligned its ChIP-seq to mm9 and quantified its RNA-seq against GENCODE M23. The plan held
one string for both ("mm9 (ChIP-seq) / GENCODE M23 (RNA-seq transcriptome)"), and stage 1 therefore
refused every reference-dependent operation for the whole paper. An annotation release also establishes
an assembly where it belongs to exactly one, and a stated release bioAF does not supply is never
quietly swapped for the one it does.
"""

import pytest

from app.services.validation_reference import (
    UNAVAILABLE,
    UNRESOLVED,
    UNSTATED,
    USABLE,
    experiment_reference,
    operation_reference,
    parse_annotation,
    supplied_references,
)

_SUPPLIED = supplied_references()


def _ref(assembly=None, annotation=None, *, organism=None, pipeline="nf-core/rnaseq", assembly_quote=None, annotation_quote=None):
    experiment = {
        "organism": organism,
        "reference": {
            "assembly": {"stated": assembly, "quote": assembly_quote},
            "annotation": {"stated": annotation, "quote": annotation_quote},
        },
    }
    return experiment_reference(experiment, pipeline_key=pipeline, supplied=_SUPPLIED)


# ---- an annotation release establishes its assembly ----


@pytest.mark.parametrize(
    "text, assembly",
    [
        ("GENCODE M23", "GRCm38"),
        ("GENCODE vM25", "GRCm38"),
        ("GENCODE M26", "GRCm39"),
        ("GENCODE M1", "mm9"),
        ("GENCODE v19", "GRCh37"),
        ("GENCODE release 32", "GRCh38"),
    ],
)
def test_a_gencode_release_belongs_to_exactly_one_assembly(text, assembly):
    assert parse_annotation(text)["assembly"] == assembly


def test_an_ensembl_release_establishes_an_assembly_only_with_its_organism():
    assert parse_annotation("Ensembl 102", organism="Mus musculus")["assembly"] == "GRCm38"
    assert parse_annotation("Ensembl release 75", organism="Homo sapiens")["assembly"] == "GRCh37"
    assert parse_annotation("Ensembl 102")["assembly"] is None


def test_a_release_whose_assembly_is_not_established_says_so():
    assert parse_annotation("Ensembl 103", organism="mouse")["assembly"] is None
    assert parse_annotation("RefSeq annotation") is None


# ---- statuses ----


def test_samd1s_rna_seq_reference_is_established_and_its_annotation_unavailable():
    """GENCODE M23 establishes GRCm38, which bioAF supplies; bioAF pins Ensembl 102 for it, not M23."""
    ref = _ref(annotation="GENCODE M23", annotation_quote="quantified against GENCODE M23")
    assert ref["assembly"]["status"] == USABLE
    assert ref["assembly"]["resolved"] == "GRCm38"
    assert ref["assembly"]["established_from"] == "annotation release"
    assert ref["annotation"]["status"] == UNAVAILABLE
    assert "Ensembl 102" in ref["annotation"]["reason"]


def test_samd1s_chip_seq_reference_is_unavailable():
    ref = _ref(assembly="mm9", pipeline="nf-core/chipseq")
    assert ref["assembly"]["status"] == UNAVAILABLE
    assert ref["assembly"]["resolved"] is None


def test_a_stated_assembly_that_contradicts_its_annotation_is_unresolved_with_both_quotes():
    ref = _ref(
        assembly="GRCh38",
        annotation="GENCODE v19",
        assembly_quote="aligned to GRCh38",
        annotation_quote="annotated with GENCODE v19",
    )
    assert ref["assembly"]["status"] == UNRESOLVED
    assert "aligned to GRCh38" in ref["assembly"]["reason"]
    assert "annotated with GENCODE v19" in ref["assembly"]["reason"]


def test_the_pinned_release_is_usable():
    ref = _ref(assembly="GRCm38", annotation="Ensembl 102", organism="Mus musculus")
    assert ref["assembly"]["status"] == USABLE
    assert ref["annotation"]["status"] == USABLE


def test_an_unstated_annotation_on_a_usable_assembly_uses_the_pinned_one_as_a_recorded_assumption():
    ref = _ref(assembly="GRCh38")
    assert ref["annotation"]["status"] == USABLE
    assert "assumption" in ref["annotation"]
    assert "Ensembl 112" in ref["annotation"]["assumption"]


def test_an_unstated_reference_is_unstated_and_the_organism_never_establishes_one():
    ref = _ref(organism="Homo sapiens")
    assert ref["assembly"]["status"] == UNSTATED
    assert ref["assembly"]["resolved"] is None


# ---- operations proceed or refuse per the dependence table ----


def test_raw_reanalysis_that_quantifies_genes_needs_the_annotation():
    ref = _ref(annotation="GENCODE M23")
    status, reason = operation_reference(ref, "raw_reanalysis", pipeline_key="nf-core/rnaseq")
    assert status == UNAVAILABLE
    assert "GENCODE M23" in reason


def test_raw_reanalysis_of_peaks_needs_the_assembly_alone():
    ref = _ref(assembly="GRCm38", annotation="GENCODE M23", pipeline="nf-core/chipseq")
    status, _ = operation_reference(ref, "raw_reanalysis", pipeline_key="nf-core/chipseq")
    assert status == USABLE


def test_processed_reanalysis_and_consistency_need_no_reference():
    ref = _ref(assembly="mm9")
    assert operation_reference(ref, "processed_reanalysis", pipeline_key="nf-core/rnaseq")[0] == USABLE
    assert operation_reference(ref, "author_results", pipeline_key="nf-core/rnaseq")[0] == USABLE


def test_an_unstated_reference_leaves_reference_dependent_checks_unresolved():
    status, reason = operation_reference(_ref(), "raw_reanalysis", pipeline_key="nf-core/rnaseq")
    assert status == UNRESOLVED
    assert reason == "the paper does not state its reference"


def test_the_declared_list_names_what_bioaf_supplies():
    pairs = {(r["assembly"], r["annotation"]) for r in _SUPPLIED}
    assert ("GRCm38", "Ensembl 102") in pairs
    assert ("GRCh38", "Ensembl 112") in pairs


def test_an_experiments_reference_blocker_names_the_experiment_and_the_part():
    from app.services.validation_reference import experiment_reference_blocker

    usable = {"status": "usable", "resolved": "GRCm38"}
    unavailable = {"status": "unavailable", "stated": "GENCODE M23",
                   "reason": "the paper states GENCODE M23; bioAF supplies Ensembl 102 for GRCm38, and never swaps one release for another"}
    experiment = {"id": "e2", "assay": "bulk RNA-seq", "workflow": "nf-core/rnaseq",
                  "reference": {"assembly": usable, "annotation": unavailable}}
    blocker = experiment_reference_blocker(experiment)
    assert "e2" in blocker and "GENCODE M23" in blocker
    assert "Checks that need no reference are unaffected" in blocker
    fine = {**experiment, "reference": {"assembly": usable, "annotation": {"status": "usable", "resolved": "Ensembl 102"}}}
    assert experiment_reference_blocker(fine) is None
