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
    # DiffBind's spelling, and it IS log2: `dba.report` emits `Fold` alongside the log2 `Conc_*`
    # group means it is the difference of (GSE157174 S1: 7.7 - 4.62 = 3.08). Last in the list so a
    # table carrying both an explicit `log2FoldChange` and a `Fold` still prefers the explicit one.
    # `_pick` matches a whole squashed header cell, never a substring, so this cannot capture a
    # linear `Fold_Change` column (which squashes to "foldchange").
    "fold",
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
_PVAL_TOKENS = ("p.value", "pvalue", "p value", "pval", "p_val")

# change_7.5 section 1.2: the stated operators, applied as stated.
_COMPARE = {
    "<": lambda value, cutoff: value < cutoff,
    "<=": lambda value, cutoff: value <= cutoff,
    ">": lambda value, cutoff: value > cutoff,
    ">=": lambda value, cutoff: value >= cutoff,
}

# A parse note that says the stated measure has no column in this table. Matched by callers that must
# not score such a table as an empty result.
MISSING_MEASURE = "cannot be applied to it"


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


def _mapped(column_map: dict | None, role: str, header_lc: list[str]) -> int | None:
    """The index a caller pinned for ``role``, or None to fall back to the alias list.

    The map is a hint (from a model in autonomous mode, or a person at the gate), not a contract: a
    name that is not in the header is ignored rather than honoured, so a wrong guess can never blank
    a table that the alias list would have parsed on its own.
    """
    if not column_map:
        return None
    name = column_map.get(role)
    if not name:
        return None
    target = _clean(str(name)).lower()
    if target in header_lc:
        return header_lc.index(target)
    squashed = [_squash(h) for h in header_lc]
    t = _squash(target)
    return squashed.index(t) if t and t in squashed else None


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


def _find_contrast_columns(header: list[str], contrast: str) -> tuple[int | None, int | None, int | None]:
    """In a wide multi-contrast table, find the lfc, padj and raw P columns for one contrast.

    spike-03: tables like `HG v NG logFC` / `HG v NG FDR` have no bare log2FoldChange
    column, so we match columns whose header contains the contrast label AND an lfc/padj token.
    change_7.5 section 1.2: a raw P column is found the same way, because a claim can be stated at P.
    """
    key = re.sub(r"\s+", " ", contrast.strip().lower())
    lfc_i = padj_i = pval_i = None
    for i, h in enumerate(header):
        hl = re.sub(r"\s+", " ", h.strip().lower())
        if key and key in hl:
            if lfc_i is None and any(t in hl for t in _LFC_TOKENS):
                lfc_i = i
            adjusted = any(t in hl for t in _PADJ_TOKENS)
            if padj_i is None and adjusted:
                padj_i = i
            elif pval_i is None and not adjusted and any(t in hl for t in _PVAL_TOKENS):
                pval_i = i
    return lfc_i, padj_i, pval_i


def _count_contrast_groups(header: list[str]) -> int:
    """Heuristic: how many distinct '<label> logFC' groups the header carries."""
    labels = set()
    for h in header:
        hl = h.strip().lower()
        for tok in _LFC_TOKENS:
            if tok in hl:
                labels.add(hl.replace(tok, "").strip(" ._-"))
    return len(labels)


def _populated(row: list[str]) -> int:
    """How many cells in a row actually carry a value."""
    return sum(1 for c in row if _clean(c))


def _strip_leading_title_rows(rows: list[list[str]]) -> list[list[str]]:
    """Drop the title banner journals put above the real header of a supplementary table.

    GSE157174's Supplementary Table S1 opens with `"Table S1 - 5,607 differentially accessible
    peaks ",,,,,,,,,,,` and only then the header. Taken verbatim, that banner WAS the header, so
    not even chrom/start/end could be located and the table yielded nothing.

    Deliberately narrow: a row is a banner only if it carries at most one value AND some later row
    carries more. A genuinely single-column table therefore keeps all of its rows.
    """
    best = max((_populated(r) for r in rows), default=0)
    if best <= 1:
        return rows
    i = 0
    while i < len(rows) and _populated(rows[i]) <= 1:
        i += 1
    return rows[i:]


def _read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    # Sniff on the widest of the first few lines, not blindly on the first: a title banner can be
    # comma-padded while the table below it is tab-separated.
    delim = _sniff_delim(max(lines[:5], key=len)) if lines else ","
    rdr = csv.reader(io.StringIO(text), delimiter=delim)
    rows = _strip_leading_title_rows([r for r in rdr if r])
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _significance_column(kind: str, padj_i: int | None, pval_i: int | None, fs: FindingSet) -> int | None:
    """The column the significance cutoff applies to, by the cutoff's kind, or None with a note.

    change_7.4 section 1.6: a table with no adjusted P column had its raw P value compared against the
    adjusted cutoff. A P value and an adjusted P value are different definitions, so a table without
    the column the cutoff names is not checkable against it, and the note says which column is missing.
    """
    if kind == "pvalue":
        if pval_i is None:
            fs.parse_notes.append(
                f"the table has no P-value column, so a P-value cutoff {MISSING_MEASURE}; "
                "bioAF does not substitute the adjusted P value"
            )
        return pval_i
    if padj_i is None:
        fs.parse_notes.append(
            f"the table has no adjusted P-value column, so an adjusted P cutoff {MISSING_MEASURE}; "
            "bioAF does not substitute the raw P value"
        )
    return padj_i


def missing_measure(fs: FindingSet) -> str | None:
    """The parse note saying the stated measure has no column in the table, or None."""
    return next((n for n in fs.parse_notes if MISSING_MEASURE in n), None)


def _passes(sig: float, lfc: float, *, padj_threshold, significance_operator, lfc_threshold, effect_operator) -> bool:
    return _COMPARE[significance_operator](sig, padj_threshold) and _COMPARE[effect_operator](abs(lfc), lfc_threshold)


def normalize_gene_table(
    text: str,
    *,
    lfc_threshold: float = 1.0,
    padj_threshold: float = 0.05,
    contrast: str | None = None,
    column_map: dict | None = None,
    significance_kind: str = "padj",
    significance_operator: str = "<=",
    effect_operator: str = ">=",
) -> FindingSet:
    """A deposited DE table as a directional FindingSet at the given cutoffs.

    ``padj_threshold`` is the significance cutoff, applied to the column ``significance_kind`` names:
    the adjusted P value (``padj``, the default) or the raw P value (``pvalue``). Never the other one.
    change_7.5 section 1.2: ``significance_operator`` and ``effect_operator`` are the stated ones
    (``<`` or ``<=`` on significance, ``>`` or ``>=`` on |log2FC|). The defaults are how the legacy
    pair was always applied, for the callers that still hold only that pair.
    """
    header, rows = _read_rows(text)
    if not header:
        return FindingSet(kind="gene", namespace="unknown", parse_notes=["empty table"])

    header_lc = [_clean(h).lower() for h in header]
    id_i = _mapped(column_map, "id", header_lc) or _pick(_ID_COLS, header_lc)
    if id_i is None:
        id_i = 0  # common: unnamed index column holds the gene id

    lfc_i = _mapped(column_map, "lfc", header_lc) or _pick(_LFC_COLS, header_lc)
    padj_i = _mapped(column_map, "padj", header_lc) or _pick(_PADJ_COLS, header_lc)
    pval_i = _mapped(column_map, "pval", header_lc) or _pick(_PVAL_COLS, header_lc)
    notes: list[str] = []

    # multi-contrast wide table: no bare lfc/padj -> need a contrast to pick the columns
    if lfc_i is None or padj_i is None:
        groups = _count_contrast_groups(header)
        if contrast:
            c_lfc, c_padj, c_pval = _find_contrast_columns(header, contrast)
            if c_lfc is not None:
                lfc_i = c_lfc
            if c_padj is not None:
                padj_i = c_padj
            if c_pval is not None:
                pval_i = c_pval
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

    sig_src = _significance_column(significance_kind, padj_i, pval_i, fs)
    if sig_src is None:
        fs.n_tested = len(rows)
        return fs

    tested = 0
    for r in rows:
        if len(r) <= max(x for x in (id_i, lfc_i, sig_src) if x is not None):
            continue
        tested += 1
        lfc = _to_float(r[lfc_i])
        sig = _to_float(r[sig_src]) if sig_src is not None else None
        if lfc is None or sig is None:
            continue
        if _passes(
            sig,
            lfc,
            padj_threshold=padj_threshold,
            significance_operator=significance_operator,
            lfc_threshold=lfc_threshold,
            effect_operator=effect_operator,
        ):
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
    column_map: dict | None = None,
    significance_kind: str = "padj",
    significance_operator: str = "<=",
    effect_operator: str = ">=",
) -> FindingSet:
    """Normalize a differential-peak table (ATAC/ChIP DA) into interval entities.

    Entity id is a genomic interval `chrom:start-end`; overlap is computed by E6, not by
    string equality.
    """
    header, rows = _read_rows(text)
    if not header:
        return FindingSet(kind="interval", namespace="interval", parse_notes=["empty table"])

    header_lc = [_clean(h).lower() for h in header]
    chrom_i = _mapped(column_map, "chrom", header_lc) or _pick(_CHROM_COLS, header_lc)
    start_i = _mapped(column_map, "start", header_lc) or _pick(_START_COLS, header_lc)
    end_i = _mapped(column_map, "end", header_lc) or _pick(_END_COLS, header_lc)
    lfc_i = _mapped(column_map, "lfc", header_lc) or _pick(_LFC_COLS, header_lc)
    padj_i = _mapped(column_map, "padj", header_lc) or _pick(_PADJ_COLS, header_lc)
    pval_i = _mapped(column_map, "pval", header_lc) or _pick(_PVAL_COLS, header_lc)

    fs = FindingSet(kind="interval", namespace="interval")
    if chrom_i is None or start_i is None or end_i is None:
        fs.parse_notes.append("could not locate chrom/start/end columns")
        return fs

    # Multi-contrast wide DA table (no bare log2FC/padj): select the ratified contrast's columns.
    # This must run BEFORE the locate check below, mirroring the gene path (previously it sat after
    # the early return and was dead code, so multi-contrast peak tables silently yielded nothing).
    if (lfc_i is None or padj_i is None) and contrast:
        c_lfc, c_padj, c_pval = _find_contrast_columns(header, contrast)
        if c_lfc is not None:
            lfc_i = c_lfc
        if c_padj is not None:
            padj_i = c_padj
        if c_pval is not None:
            pval_i = c_pval

    if lfc_i is None or (padj_i is None and pval_i is None):
        fs.parse_notes.append("could not locate log2FC and/or significance columns")
        return fs

    sig_src = _significance_column(significance_kind, padj_i, pval_i, fs)
    if sig_src is None:
        return fs
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
        if _passes(
            sig,
            lfc,
            padj_threshold=padj_threshold,
            significance_operator=significance_operator,
            lfc_threshold=lfc_threshold,
            effect_operator=effect_operator,
        ):
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
