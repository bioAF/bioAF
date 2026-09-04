"""B4 result-set normalizer (lit_validation Level-3, C2).

Parses deposited differential tables into a normalized directional FindingSet, across the
real-world formats spike-03 surfaced: DESeq2 CSV with gene symbols, Ensembl TSV, wide
multi-contrast tables (contrast selection), and differential-peak interval tables.
"""

from app.services.result_set_normalizer import normalize_gene_table, normalize_interval_table

# a DESeq2 CSV with an unnamed index (gene symbol) column, like GSE255007
_DESEQ2_CSV = (
    ",baseMean,log2FoldChange,lfcSE,stat,pvalue,padj,change\n"
    "MT-ND6,24266,5.75,0.09,61.4,0,0.0,UP\n"  # up, significant
    "GAPDH,5000,0.10,0.05,2.0,0.2,0.30,NS\n"  # |lfc| below + padj above -> excluded
    "TP53,1200,-2.40,0.10,-24,0,0.001,DOWN\n"  # down, significant
)

# an Ensembl TSV with DESeq2-annot column names, like GSE270036
_ENSEMBL_TSV = (
    "gene_id\tgene_name\tlog2fc\tpvalue\tpadjust\n"
    "ENSMUSG00000005087\tRsph14\t1.72\t0.0\t0.001\n"  # up sig
    "ENSMUSG00000079083\tJrkl\t-0.05\t0.8\t0.9\n"  # excluded
    "ENSMUSG00000027562\tGeneX\t-1.11\t0.0\t0.01\n"  # down sig
)

# a wide multi-contrast table, like GSE238008: two contrasts side by side, no bare log2FC
_MULTI_CONTRAST = (
    "gene\tHG v NG logFC\tHG v NG FDR\tHG_CD200 v HG logFC\tHG_CD200 v HG FDR\n"
    "AAA\t2.0\t0.001\t0.1\t0.9\n"  # sig ONLY in HG v NG
    "BBB\t0.1\t0.9\t3.0\t0.001\n"  # sig ONLY in HG_CD200 v HG
)

_INTERVAL = (
    "chr\tstart\tend\tlog2FoldChange\tpadj\n"
    "chr1\t1000\t2000\t2.5\t0.01\n"  # up sig
    "chr2\t5000\t6000\t-3.0\t0.001\n"  # down sig
    "chr3\t7000\t8000\t0.2\t0.5\n"  # excluded
)


def test_normalize_deseq2_csv_symbols():
    fs = normalize_gene_table(_DESEQ2_CSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "symbol"
    ids = fs.directions()
    assert ids == {"MT-ND6": "up", "TP53": "down"}
    assert fs.n_tested == 3


def test_normalize_ensembl_tsv():
    fs = normalize_gene_table(_ENSEMBL_TSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "ensembl_gene"
    assert fs.directions() == {"ENSMUSG00000005087": "up", "ENSMUSG00000027562": "down"}


def test_non_significant_excluded():
    fs = normalize_gene_table(_DESEQ2_CSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert "GAPDH" not in fs.directions()


def test_multi_contrast_requires_contrast_selection():
    # no contrast -> ambiguous, records a note, selects nothing
    fs = normalize_gene_table(_MULTI_CONTRAST)
    assert fs.entities == []
    assert any("contrast" in n.lower() for n in fs.parse_notes)


def test_multi_contrast_selects_named_contrast():
    fs = normalize_gene_table(_MULTI_CONTRAST, contrast="HG v NG")
    # only AAA is significant in the HG v NG contrast (not BBB)
    assert fs.directions() == {"AAA": "up"}


def test_multi_contrast_other_contrast_selects_differently():
    fs = normalize_gene_table(_MULTI_CONTRAST, contrast="HG_CD200 v HG")
    assert fs.directions() == {"BBB": "up"}


def test_normalize_interval_table():
    fs = normalize_interval_table(_INTERVAL, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.kind == "interval"
    dirs = fs.directions()
    assert dirs == {"chr1:1000-2000": "up", "chr2:5000-6000": "down"}


# The EXACT header the headless DA template (da_peaks_deseq2.ipynb) writes to /outputs. This pins the
# template<->normalizer contract: if either side's columns drift, this round-trip breaks locally.
_DA_TEMPLATE_OUTPUT = (
    "chr,start,end,log2FoldChange,padj\n"
    "chr1,1000,2000,2.5,0.001\n"  # up sig
    "chr2,5000,6000,-1.8,0.01\n"  # down sig
    "chr3,100,200,0.1,0.9\n"  # excluded
)

# a wide multi-contrast DA table: two contrasts side by side, no bare log2FC column
_MULTI_CONTRAST_INTERVAL = (
    "chr\tstart\tend\tKO v WT logFC\tKO v WT FDR\tTREAT v WT logFC\tTREAT v WT FDR\n"
    "chr1\t1000\t2000\t2.0\t0.001\t0.1\t0.9\n"  # sig ONLY in KO v WT
    "chr2\t5000\t6000\t0.1\t0.9\t3.0\t0.001\n"  # sig ONLY in TREAT v WT
)


# A DESeq2 export in the MDPI-SI style (real: GSE309060 / jfb17020057 Table S1): GeneType is the
# FIRST column, with GeneSymbol + ENSG alongside. The id must resolve to the gene symbol, not the
# GeneType (which would make every entity id "protein_coding" and score 0 overlap against a real set).
_GENESYMBOL_TABLE = (
    '"GeneType"\t"GeneSymbol"\t"ENSG"\t"log2FoldChange"\t"pvalue"\t"padj"\n'
    'protein_coding\tCEMIP\t"ENSG00000103888.16"\t4.41\t5.6e-11\t4.99e-08\n'  # up, significant
    'protein_coding\tGAPDH\t"ENSG00000111640.14"\t0.10\t0.5\t0.9\n'  # excluded
    'protein_coding\tADM2\t"ENSG00000128165.8"\t-4.94\t3.3e-17\t1.75e-13\n'  # down, significant
)


def test_normalize_genesymbol_genetype_columns_picks_the_symbol():
    fs = normalize_gene_table(_GENESYMBOL_TABLE, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "symbol"
    assert fs.directions() == {"CEMIP": "up", "ADM2": "down"}


def test_normalize_da_template_output_roundtrips():
    # The DA template's own output columns (chr/start/end/log2FoldChange/padj) normalize cleanly.
    fs = normalize_interval_table(_DA_TEMPLATE_OUTPUT, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.kind == "interval"
    assert fs.directions() == {"chr1:1000-2000": "up", "chr2:5000-6000": "down"}


def test_normalize_interval_multi_contrast_selects_named_contrast():
    # A wide DA table with no bare log2FC must select the ratified contrast's columns (the interval
    # path must do this BEFORE giving up, like the gene path).
    fs = normalize_interval_table(_MULTI_CONTRAST_INTERVAL, contrast="KO v WT")
    assert fs.directions() == {"chr1:1000-2000": "up"}  # only the KO v WT-significant peak

    fs2 = normalize_interval_table(_MULTI_CONTRAST_INTERVAL, contrast="TREAT v WT")
    assert fs2.directions() == {"chr2:5000-6000": "up"}


def test_normalize_interval_multi_contrast_without_selection_records_note():
    fs = normalize_interval_table(_MULTI_CONTRAST_INTERVAL)
    assert fs.entities == []
    assert any("log2fc" in n.lower() or "contrast" in n.lower() for n in fs.parse_notes)


def test_to_dict_shape():
    fs = normalize_gene_table(_DESEQ2_CSV)
    d = fs.to_dict()
    assert d["n_up"] == 1 and d["n_down"] == 1 and d["n_sig"] == 2
    assert d["namespace"] == "symbol"


# ---- miRNA identifiers (plan_1 step 2, nf-core/smrnaseq) ----

# A deposited small-RNA DESeq2 table. The id column is NOT first: mirtop-derived tables commonly
# carry baseMean ahead of it, and the column-0 fallback would take baseMean as the identifier and
# produce a finding set of numbers.
_MIRNA_CSV = (
    "baseMean,miRNA,log2FoldChange,lfcSE,pvalue,padj\n"
    "24266,hsa-miR-21-5p,5.75,0.09,0,0.0\n"  # up, significant
    "5000,hsa-let-7a-5p,0.10,0.05,0.2,0.30\n"  # excluded
    "1200,hsa-miR-155-3p,-2.40,0.10,0,0.001\n"  # down, significant
)


def test_a_mirna_table_is_keyed_by_its_declared_id_column():
    """`miRNA` is recognised by declaration. Falling back to column 0 here would key the finding set
    on baseMean, which parses, compares, and is meaningless."""
    fs = normalize_gene_table(_MIRNA_CSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.directions() == {"hsa-miR-21-5p": "up", "hsa-miR-155-3p": "down"}
    assert fs.n_tested == 3


def test_mirna_identifiers_survive_normalization_unchanged():
    """miRBase ids carry meaning in their case and suffix: -5p and -3p are the two arms of the same
    hairpin and are different molecules. Nothing may upper-case or truncate them."""
    fs = normalize_gene_table(_MIRNA_CSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert "hsa-miR-21-5p" in fs.directions()
    assert "HSA-MIR-21-5P" not in fs.directions()


def test_a_mirna_table_declares_the_mirbase_namespace():
    """Not `symbol`. A paper that deposits HGNC symbols (MIR21) and a run that reports miRBase ids
    (hsa-miR-21-5p) share no identifier, and comparing them without saying so turns an unmapped
    namespace into a false divergence. The concordance service already refuses across namespaces."""
    fs = normalize_gene_table(_MIRNA_CSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "mirbase"


def test_gene_symbol_tables_are_still_symbols():
    """Regression: the miRBase pattern must not claim ordinary gene symbols."""
    assert normalize_gene_table(_DESEQ2_CSV, lfc_threshold=1.0, padj_threshold=0.05).namespace == "symbol"
    assert normalize_gene_table(_ENSEMBL_TSV, lfc_threshold=1.0, padj_threshold=0.05).namespace == "ensembl_gene"


# ---- real deposited tables, punctuation and all (plan_1 step 5) ----

# The first eight rows of GSE327014's deposited differential table, byte-for-byte from GEO
# (`GSE327014_C8_NC_1_C8_NC_2--C8_HS_1_C8_HS_2.different.miRNA.txt`). Nothing here is invented: it
# is what a real small-RNA study actually deposits, and every existing candidate column name missed
# it on punctuation alone. `log2(Fold_change)` is not `log2foldchange`, and `p-value` is not
# `p.value`. There is no adjusted-p column at all, which is common in this literature.
_GEO_MIRNA_TSV = (
    "miRNA\tC8_NC_1\tC8_NC_2\tC8_HS_1\tC8_HS_2\tlog2(Fold_change)\tp-value\tWebsite\n"
    "mmu-miR-139-3p\t476.8\t138.4\t41.0\t49.0\t-2.76862735668505\t1.68743585704358e-05\thttp://x\n"
    "mmu-miR-5099\t994.3\t802.6\t230.7\t446.4\t-1.40921173132414\t0.000349278194604892\thttp://x\n"
    "mmu-miR-341-3p\t10.0\t12.0\t80.0\t95.0\t2.9\t0.0001\thttp://x\n"
    "mmu-let-7a-5p\t500.0\t520.0\t505.0\t515.0\t0.05\t0.9\thttp://x\n"
    "mmu-miR-99b-5p\t300.0\t280.0\t150.0\t160.0\t-0.9\t0.001\thttp://x\n"
)


def test_a_real_geo_mirna_table_parses_despite_its_punctuation():
    """Found by taking a real study to a verdict, not by unit testing. Column matching was exact on
    the lowercased header, so `log2(Fold_change)` and `p-value` both missed and the whole table came
    back with zero significant entities and a note nobody would have read as "bioAF cannot read
    this format"."""
    fs = normalize_gene_table(_GEO_MIRNA_TSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "mirbase"
    assert fs.n_tested == 5
    assert fs.directions() == {
        "mmu-miR-139-3p": "down",
        "mmu-miR-5099": "down",
        "mmu-miR-341-3p": "up",
    }


def test_a_table_with_only_a_raw_p_value_says_so():
    """Small-RNA studies routinely deposit an unadjusted p-value. Using it is the right call, since
    the alternative is refusing every table in the subfield, but the verdict has to carry the
    caveat rather than imply an FDR that was never computed."""
    fs = normalize_gene_table(_GEO_MIRNA_TSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert any("raw p-value" in n for n in fs.parse_notes), fs.parse_notes


def test_punctuation_normalization_does_not_blur_distinct_columns():
    """The fix strips punctuation before matching, so it must not make two different columns look
    like the same one. A table carrying both a nominal and an adjusted p must still prefer the
    adjusted one."""
    both = (
        "gene\tlog2FoldChange\tp-value\tadj.P.Val\n"
        "AAA\t2.0\t0.001\t0.01\n"  # significant on both
        "BBB\t2.0\t0.001\t0.90\n"  # significant on the nominal p ONLY
    )
    fs = normalize_gene_table(both, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.directions() == {"AAA": "up"}
    assert not any("raw p-value" in n for n in fs.parse_notes)


# Seurat's FindMarkers, the dominant single-cell DE output. Real header and real rows, taken from
# GSE312719_DE_analysis_SingleCellRNAseq.xlsx (10.1016/j.celrep.2026.117031), sheet
# "HU_Inf_MF vs HU_control".
_SEURAT_FINDMARKERS_TSV = (
    "gene\tp_val\tavg_log2FC\tpct.1\tpct.2\tp_val_adj\n"
    "PARP9\t3.3297162819680302E-274\t1.7295183630975901\t1\t1\t1.2854702678165799E-269\n"
    "IFIT2\t1.5446925589325199E-183\t1.29896871601505\t1\t1\t5.96344009301487E-179\n"
    "TENT5A\t8.9903339418906596E-154\t1.1210216174196399\t1\t1\t3.4708083216063101E-149\n"
    "QUIET\t0.4\t0.02\t1\t1\t0.98\n"
    "DOWNGENE\t1.0E-40\t-2.5\t1\t1\t1.0E-36\n"
    "SUBTHRESHOLD\t1.0E-40\t0.3\t1\t1\t1.0E-36\n"
)


def test_a_seurat_findmarkers_table_parses():
    """`avg_log2FC` and `p_val_adj` are the two columns almost every single-cell paper deposits, and
    both missed. This is the SAME silent failure as the GSE327014 punctuation defect -- table parses,
    every row read, zero entities out -- but `_squash` cannot reach it: these are not punctuation
    variants, they are the canonical name with an affix, and matching is on the whole squashed name.

    Seurat is the dominant single-cell analysis tool, so this was scRNA-seq's ground-truth path
    failing for the most common format there is."""
    fs = normalize_gene_table(_SEURAT_FINDMARKERS_TSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.namespace == "symbol"
    assert fs.directions() == {
        "PARP9": "up",
        "IFIT2": "up",
        "TENT5A": "up",
        "DOWNGENE": "down",
    }


def test_a_seurat_table_uses_the_adjusted_p_not_the_nominal_one():
    """FindMarkers deposits BOTH `p_val` and `p_val_adj`, which squash to different strings but sit
    next to each other. Reading the nominal one would silently loosen the paper's own threshold and
    inflate its ground-truth set."""
    fs = normalize_gene_table(_SEURAT_FINDMARKERS_TSV, lfc_threshold=1.0, padj_threshold=0.05)
    assert not any("raw p-value" in n for n in fs.parse_notes), fs.parse_notes


def test_the_older_seurat_spelling_parses_too():
    """Seurat v3 and earlier wrote `avg_logFC`. Papers deposited under it are still being validated."""
    old = "gene\tp_val\tavg_logFC\tp_val_adj\nAAA\t1e-30\t2.0\t1e-26\nBBB\t1e-30\t-2.0\t1e-26\n"
    fs = normalize_gene_table(old, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.directions() == {"AAA": "up", "BBB": "down"}


# The EXACT shape of a DiffBind/edgeR differential-accessibility deposit: Supplementary Table S1 of
# 10.1038/s41598-021-93509-w (GSE157174, quiescent vs activated naive CD4+ T-cells), which is the
# ATAC ground truth this feature is validated against.
#
# Two things about it defeated the parser, and neither is exotic:
#   1. DiffBind names its log2 fold change column `Fold`, not `log2FoldChange`. It IS log2 (the
#      header's own `Conc_CD3pos - Conc_CD3neg` equals it: 7.7 - 4.62 = 3.08), so it must be read
#      as one rather than skipped.
#   2. The published CSV opens with a one-cell TITLE row above the real header, which is how
#      journals ship supplementary tables.
_DIFFBIND_S1 = (
    '"Table S1 - 5,607 differentially accessible peaks ",,,,,,,,,,,\n'
    ",seqnames,start,end,width,strand,Conc,Conc_CD3pos,Conc_CD3neg,Fold,p.value,FDR\n"
    "114621,chr7,105809051,105809885,835,*,6.86,7.7,4.62,3.08,9.86E-41,1.29E-35\n"  # up, significant
    "127702,chr9,127396571,127397615,1045,*,7.05,7.88,4.82,3.07,5.96E-36,3.91E-31\n"  # up, significant
    "113048,chr7,68326932,68327384,453,*,2.85,1.46,3.54,-2.09,0.0158,0.0483\n"  # down, significant
)


def test_normalize_interval_reads_diffbind_fold_column():
    # `Fold` is DiffBind's spelling of log2FC. Without it the table parses, every row is read, and
    # zero entities come out, which scores as `not_computed` rather than as a disagreement.
    body = _DIFFBIND_S1.split("\n", 1)[1]
    fs = normalize_interval_table(body, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.kind == "interval"
    assert fs.directions() == {
        "chr7:105809051-105809885": "up",
        "chr9:127396571-127397615": "up",
        "chr7:68326932-68327384": "down",
    }


def test_normalize_interval_reads_diffbind_fold_prefers_fdr_over_raw_p():
    # The table carries BOTH `p.value` and `FDR`. The adjusted column must win: the third row's raw
    # p (0.0158) and FDR (0.0483) are both under 0.05 here, so pin the effect sizes instead, which
    # are what a wrong-column match would corrupt.
    body = _DIFFBIND_S1.split("\n", 1)[1]
    fs = normalize_interval_table(body, lfc_threshold=1.0, padj_threshold=0.05)
    sigs = {e.id: e.significance for e in fs.entities}
    assert sigs["chr7:68326932-68327384"] == 0.0483


def test_normalize_interval_skips_a_supplementary_title_row():
    # As published: a single-cell title above the real header. Fed verbatim, the title row was taken
    # AS the header, so even chrom/start/end could not be located.
    fs = normalize_interval_table(_DIFFBIND_S1, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.directions() == {
        "chr7:105809051-105809885": "up",
        "chr9:127396571-127397615": "up",
        "chr7:68326932-68327384": "down",
    }


# ---- a caller-supplied column map, for when the alias list does not recognise the header ----
#
# `_pick` matches a squashed header cell against an enumerated alias list, so it only knows the
# spellings somebody wrote down. A real csaw deposit (GSE273743, the NKX2.2 ChIP-seq series) names
# its columns `regions.seqnames` / `regions.start` / `regions.end` / `combined.FDR` /
# `combined.rep.logFC`, none of which is in any list, and the whole table parsed to zero entities
# under the note "could not locate chrom/start/end columns". csaw and DiffBind both prefix their
# columns, so this is a family of deposits rather than one odd file.

_CSAW_HEADER = (
    '"","regions.seqnames","regions.start","regions.end","regions.width","regions.strand",'
    '"combined.PValue","combined.FDR","combined.direction","combined.rep.logFC",'
    '"best.PValue","best.FDR","best.rep.logFC"'
)
_CSAW = (
    _CSAW_HEADER + "\n"
    '"1","1",3361051,3361210,160,"*",0.795,0.99,"down",-0.116,1,1,-0.201\n'
    '"2","2",4857701,4857760,60,"*",0.0004,0.004,"up",2.31,0.11,0.68,0.58\n'
    '"3","3",5000000,5000500,500,"*",0.0001,0.001,"down",-3.02,0.05,0.2,-2.9\n'
)


def test_a_csaw_table_still_fails_without_help():
    """The baseline this exists to fix. Recorded so the fix is not mistaken for a no-op."""
    fs = normalize_interval_table(_CSAW, lfc_threshold=1.0, padj_threshold=0.05)
    assert fs.entities == []
    assert any("could not locate" in n for n in fs.parse_notes)


def test_a_column_map_lets_a_csaw_table_parse():
    """Given the mapping, the same table normalizes with no other change."""
    fs = normalize_interval_table(
        _CSAW,
        lfc_threshold=1.0,
        padj_threshold=0.05,
        column_map={
            "chrom": "regions.seqnames",
            "start": "regions.start",
            "end": "regions.end",
            "lfc": "combined.rep.logFC",
            "padj": "combined.FDR",
        },
    )
    assert [e.id for e in fs.entities] == ["2:4857701-4857760", "3:5000000-5000500"]
    assert [e.direction for e in fs.entities] == ["up", "down"]


def test_a_column_map_may_name_only_what_the_alias_list_missed():
    """Partial maps are the common case: usually only the chrom/start/end prefix is unrecognised."""
    text = _CSAW.replace("combined.rep.logFC", "log2FoldChange").replace("combined.FDR", "padj")
    fs = normalize_interval_table(
        text,
        column_map={"chrom": "regions.seqnames", "start": "regions.start", "end": "regions.end"},
    )
    assert len(fs.entities) == 2


def test_a_column_map_naming_a_column_that_is_not_there_is_ignored():
    """The map is a hint from a model or a person, not a contract. A wrong name must fall back to
    the alias list rather than blanking a table that would otherwise have parsed."""
    text = _CSAW.replace("regions.seqnames", "chrom").replace("regions.start", "start")
    text = text.replace("regions.end", "end").replace("combined.rep.logFC", "log2FoldChange")
    text = text.replace("combined.FDR", "padj")
    fs = normalize_interval_table(text, column_map={"chrom": "not_a_column"})
    assert len(fs.entities) == 2


def test_a_column_map_works_for_gene_tables_too():
    """The same `_pick` sits in the gene path, so a prefixed DEG table fails identically."""
    text = "res.gene_symbol,res.log2FoldChange,res.padj\nTP53,2.4,0.001\nMYC,-3.1,0.0004\nACTB,0.1,0.9\n"
    assert normalize_gene_table(text).entities == []
    fs = normalize_gene_table(
        text,
        column_map={"id": "res.gene_symbol", "lfc": "res.log2FoldChange", "padj": "res.padj"},
    )
    assert {e.id for e in fs.entities} == {"TP53", "MYC"}
