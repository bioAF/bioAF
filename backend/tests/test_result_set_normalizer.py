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


def test_to_dict_shape():
    fs = normalize_gene_table(_DESEQ2_CSV)
    d = fs.to_dict()
    assert d["n_up"] == 1 and d["n_down"] == 1 and d["n_sig"] == 2
    assert d["namespace"] == "symbol"
