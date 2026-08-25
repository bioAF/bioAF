"""B4 (parse half): normalize a deposited result table into a FindingSet.

Turns a paper's deposited differential result (a DEG table for RNA, a differential-peak
table for ATAC/ChIP) into a normalized set of directional entities we can compare against
our own re-run (E6). Column-, delimiter-, and namespace-detecting, because deposited tables
are heterogeneous (spike-03: CSV / TSV / ".xls" that is really tab-text; gene symbols vs
Ensembl; single- vs multi-contrast wide tables).

This is the deterministic PARSE half of B4. Acquisition (fetching the table from journal SI /
GEO) and human-confirm at C1 live elsewhere; this module is pure logic so it is fully tested
locally. On ambiguity it records a parse note and degrades to an empty/honest set rather than
guessing, so the human confirm step has something explicit to correct.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

# candidate column names across DESeq2 / edgeR / limma depositor conventions
# Seurat's `FindMarkers` spellings are listed explicitly because `_squash` cannot reach them:
# matching compares the WHOLE squashed name, and these are the canonical name with an affix
# (`avg_log2FC`, `p_val_adj`) rather than a punctuation variant of it. Seurat is the dominant
# single-cell analysis tool, so this is the most common shape a scRNA-seq paper deposits, and
# missing it failed the same silent way the punctuation defect did: table parses, every row read,
# zero entities out. `avg_logFC` is the v3-and-earlier spelling, still in the literature.
_LFC_COLS = [
    "log2foldchange",
    "log2fc",
    "logfc",
    "log2.fold.change",
    "lfc",
    "coef",
    "logfoldchange",
    "avg_log2fc",
    "avg_logfc",
]
_PADJ_COLS = [
    "padj",
    "adj.p.val",
    "adjpvalue",
    "fdr",
    "qvalue",
    "q.value",
    "padjust",
    "p.adjust",
    "adj_pval",
    "adj.pvalue",
    # Last: a Seurat table carries `p_val` too, and this must never be reached for that one.
    "p_val_adj",
]
_PVAL_COLS = ["pvalue", "p.value", "pval", "p_val"]
# Order encodes PREFERENCE (_pick returns the first of these present in the header), so gene-symbol
# aliases come before Ensembl aliases: our nf-core/salmon output is symbol-keyed, and a table that
# carries both (e.g. MDPI-SI DESeq2 exports with GeneSymbol + ENSG) should match on the symbol.
_ID_COLS = [
    # miRNA identifiers first: a column literally named `mirna` is a stronger declaration than the
    # generic `id`/`feature`, and small-RNA tables often carry both.
    "mirna",
    "mirna_id",
    "mir",
    "mature_mirna",
    "mirna_name",
    "gene",
    "gene_id",
    "geneid",
    "gene_symbol",
    "genesymbol",
    "symbol",
    "genename",
    "gene_name",
    "id",
    "ensembl",
    "ensembl_gene",
    "ensembl_id",
    "ensg",
    "feature",
    "",
]
_CHROM_COLS = ["chr", "chrom", "chromosome", "seqnames"]
_START_COLS = ["start", "chromstart", "peak_start"]
_END_COLS = ["end", "chromend", "peak_end", "stop"]

_LFC_TOKENS = ("log2foldchange", "log2fc", "logfc", "log2 fold", "logfoldchange", "fold change", "logratio")
_PADJ_TOKENS = ("padj", "fdr", "adj.p", "adjp", "q.value", "qvalue", "p.adjust", "adjusted p")


@dataclass
class FindingEntity:
    id: str
    direction: str | None = None  # "up" | "down"
    effect_size: float | None = None  # log2 fold change
    significance: float | None = None  # padj / FDR


@dataclass
class FindingSet:
    kind: str  # "gene" | "interval"
    namespace: str  # "symbol" | "ensembl_gene" | "entrez" | "mirbase" | "interval" | "unknown"
    entities: list[FindingEntity] = field(default_factory=list)
    n_tested: int = 0
    parse_notes: list[str] = field(default_factory=list)

    def directions(self) -> dict[str, str | None]:
        return {e.id: e.direction for e in self.entities}

    @classmethod
    def from_dict(cls, d: dict) -> "FindingSet":
        """Reconstruct a FindingSet from its ``to_dict`` form (e.g. stored in evidence_json)."""
        return cls(
            kind=d.get("kind", "gene"),
            namespace=d.get("namespace", "unknown"),
            n_tested=d.get("n_tested", 0),
            parse_notes=list(d.get("parse_notes", [])),
            entities=[
                FindingEntity(
                    id=e["id"],
                    direction=e.get("direction"),
                    effect_size=e.get("effect_size"),
                    significance=e.get("significance"),
                )
                for e in d.get("entities", [])
            ],
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "n_tested": self.n_tested,
            "n_sig": len(self.entities),
            "n_up": sum(1 for e in self.entities if e.direction == "up"),
            "n_down": sum(1 for e in self.entities if e.direction == "down"),
            "parse_notes": self.parse_notes,
            "entities": [
                {"id": e.id, "direction": e.direction, "effect_size": e.effect_size, "significance": e.significance}
                for e in self.entities
            ],
        }


def _sniff_delim(first_line: str) -> str:
    return "\t" if "\t" in first_line else ","


def _clean(cell: str) -> str:
    return cell.strip().strip('"').strip()


def _to_float(v: str) -> float | None:
    v = v.strip()
    if v in ("", "NA", "NaN", "nan", "NULL", "None", "#N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


# miRBase names a mature miRNA as <3-4 letter species>-<miR|mir|let>-<number>, optionally with an
# arm suffix (-5p / -3p) that makes it a different molecule from its partner. They are NOT gene
# symbols: a paper depositing HGNC symbols (MIR21) and a run reporting miRBase ids (hsa-miR-21-5p)
# share no identifier at all, so calling both "symbol" turns an unmapped namespace into a false
# divergence. Named honestly, the concordance service's existing namespace guard refuses instead.
_MIRBASE_RE = re.compile(r"[a-z]{3,4}-(mir|let)-?\d", re.IGNORECASE)


def _detect_namespace(ids: list[str]) -> str:
    sample = [i for i in ids[:200] if i]
    if not sample:
        return "unknown"
    ens = sum(1 for i in sample if re.match(r"ENS[A-Z]*G\d{6,}", i))
    entrez = sum(1 for i in sample if re.fullmatch(r"\d+", i))
    mirbase = sum(1 for i in sample if _MIRBASE_RE.match(i))
    if ens > len(sample) * 0.5:
        return "ensembl_gene"
    if entrez > len(sample) * 0.5:
        return "entrez"
    if mirbase > len(sample) * 0.5:
        return "mirbase"
    return "symbol"


def _squash(name: str) -> str:
    """A column name reduced to its letters and digits, so punctuation cannot hide it.

    Real deposited tables spell the same column every way there is: `log2(Fold_change)`,
    `log2.fold.change`, `log2FoldChange`; `p-value`, `p.value`, `p_val`. Exact matching on the
    lowercased header missed all the variants nobody had happened to enumerate, and the failure was
    silent: the table parsed, every row was read, and zero entities came out.

    Found by taking a real GEO deposit (GSE327014) to a verdict rather than by unit testing.
    """
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _pick(cands: list[str], header_lc: list[str]) -> int | None:
    """The index of the first candidate present in the header, punctuation ignored.

    Order encodes preference, so an exact pass runs first: a table carrying BOTH a nominal and an
    adjusted p must still match the adjusted one on its own name rather than on whichever squashes
    to the same string first.
    """
    for c in cands:
        if c in header_lc:
            return header_lc.index(c)
    squashed = [_squash(h) for h in header_lc]
    for c in cands:
        target = _squash(c)
        # An empty candidate is the unnamed-index convention and must keep matching only a
        # genuinely empty header cell, never every punctuation-only one.
        if not target:
            continue
        if target in squashed:
            return squashed.index(target)
    return None


def _find_contrast_columns(header: list[str], contrast: str) -> tuple[int | None, int | None]:
    """In a wide multi-contrast table, find the lfc + padj columns for one contrast.

    spike-03: tables like `HG v NG logFC` / `HG v NG FDR` have no bare log2FoldChange
    column, so we match columns whose header contains the contrast label AND an lfc/padj token.
    """
    key = re.sub(r"\s+", " ", contrast.strip().lower())
    lfc_i = padj_i = None
    for i, h in enumerate(header):
        hl = re.sub(r"\s+", " ", h.strip().lower())
        if key and key in hl:
            if lfc_i is None and any(t in hl for t in _LFC_TOKENS):
                lfc_i = i
            if padj_i is None and any(t in hl for t in _PADJ_TOKENS):
                padj_i = i
    return lfc_i, padj_i


def _count_contrast_groups(header: list[str]) -> int:
    """Heuristic: how many distinct '<label> logFC' groups the header carries."""
    labels = set()
    for h in header:
        hl = h.strip().lower()
        for tok in _LFC_TOKENS:
            if tok in hl:
                labels.add(hl.replace(tok, "").strip(" ._-"))
    return len(labels)


def _read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    first_line = text.split("\n", 1)[0]
    delim = _sniff_delim(first_line)
    rdr = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in rdr if r]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def normalize_gene_table(
    text: str,
    *,
    lfc_threshold: float = 1.0,
    padj_threshold: float = 0.05,
    contrast: str | None = None,
) -> FindingSet:
    header, rows = _read_rows(text)
    if not header:
        return FindingSet(kind="gene", namespace="unknown", parse_notes=["empty table"])

    header_lc = [_clean(h).lower() for h in header]
    id_i = _pick(_ID_COLS, header_lc)
    if id_i is None:
        id_i = 0  # common: unnamed index column holds the gene id

    lfc_i = _pick(_LFC_COLS, header_lc)
    padj_i = _pick(_PADJ_COLS, header_lc)
    pval_i = _pick(_PVAL_COLS, header_lc)
    notes: list[str] = []

    # multi-contrast wide table: no bare lfc/padj -> need a contrast to pick the columns
    if lfc_i is None or padj_i is None:
        groups = _count_contrast_groups(header)
        if contrast:
            c_lfc, c_padj = _find_contrast_columns(header, contrast)
            if c_lfc is not None:
                lfc_i = c_lfc
            if c_padj is not None:
                padj_i = c_padj
            if c_lfc is None:
                notes.append(f"contrast '{contrast}' not found among columns")
        elif groups > 1:
            notes.append(f"{groups} contrasts present; specify one to select its columns")

    ids_all = [_clean(r[id_i]) for r in rows if len(r) > id_i]
    namespace = _detect_namespace(ids_all)

    fs = FindingSet(kind="gene", namespace=namespace, parse_notes=notes)
    if lfc_i is None or (padj_i is None and pval_i is None):
        fs.parse_notes.append("could not locate log2FC and/or significance columns")
        fs.n_tested = len(rows)
        return fs

    sig_src = padj_i if padj_i is not None else pval_i
    if padj_i is None:
        fs.parse_notes.append("no adjusted-p column; used raw p-value")

    tested = 0
    for r in rows:
        if len(r) <= max(x for x in (id_i, lfc_i, sig_src) if x is not None):
            continue
        tested += 1
        lfc = _to_float(r[lfc_i])
        sig = _to_float(r[sig_src]) if sig_src is not None else None
        if lfc is None or sig is None:
            continue
        if sig <= padj_threshold and abs(lfc) >= lfc_threshold:
            fs.entities.append(
                FindingEntity(
                    id=_clean(r[id_i]),
                    direction="up" if lfc > 0 else "down",
                    effect_size=lfc,
                    significance=sig,
                )
            )
    fs.n_tested = tested
    return fs


def normalize_interval_table(
    text: str,
    *,
    lfc_threshold: float = 1.0,
    padj_threshold: float = 0.05,
    contrast: str | None = None,
) -> FindingSet:
    """Normalize a differential-peak table (ATAC/ChIP DA) into interval entities.

    Entity id is a genomic interval `chrom:start-end`; overlap is computed by E6, not by
    string equality.
    """
    header, rows = _read_rows(text)
    if not header:
        return FindingSet(kind="interval", namespace="interval", parse_notes=["empty table"])

    header_lc = [_clean(h).lower() for h in header]
    chrom_i = _pick(_CHROM_COLS, header_lc)
    start_i = _pick(_START_COLS, header_lc)
    end_i = _pick(_END_COLS, header_lc)
    lfc_i = _pick(_LFC_COLS, header_lc)
    padj_i = _pick(_PADJ_COLS, header_lc)
    pval_i = _pick(_PVAL_COLS, header_lc)

    fs = FindingSet(kind="interval", namespace="interval")
    if chrom_i is None or start_i is None or end_i is None:
        fs.parse_notes.append("could not locate chrom/start/end columns")
        return fs

    # Multi-contrast wide DA table (no bare log2FC/padj): select the ratified contrast's columns.
    # This must run BEFORE the locate check below, mirroring the gene path (previously it sat after
    # the early return and was dead code, so multi-contrast peak tables silently yielded nothing).
    if (lfc_i is None or padj_i is None) and contrast:
        c_lfc, c_padj = _find_contrast_columns(header, contrast)
        if c_lfc is not None:
            lfc_i = c_lfc
        if c_padj is not None:
            padj_i = c_padj

    if lfc_i is None or (padj_i is None and pval_i is None):
        fs.parse_notes.append("could not locate log2FC and/or significance columns")
        return fs

    sig_src = padj_i if padj_i is not None else pval_i
    tested = 0
    for r in rows:
        need = max(x for x in (chrom_i, start_i, end_i, lfc_i, sig_src) if x is not None)
        if len(r) <= need:
            continue
        tested += 1
        try:
            start = int(float(r[start_i]))
            end = int(float(r[end_i]))
        except ValueError:
            continue
        lfc = _to_float(r[lfc_i])
        sig = _to_float(r[sig_src]) if sig_src is not None else None
        if lfc is None or sig is None:
            continue
        if sig <= padj_threshold and abs(lfc) >= lfc_threshold:
            chrom = _clean(r[chrom_i])
            fs.entities.append(
                FindingEntity(
                    id=f"{chrom}:{start}-{end}",
                    direction="up" if lfc > 0 else "down",
                    effect_size=lfc,
                    significance=sig,
                )
            )
    fs.n_tested = tested
    return fs
