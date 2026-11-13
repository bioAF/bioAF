"""Numeric and FastQC-section helpers shared by the tailored QC templates.

bulk RNA-seq, ChIP-seq and ATAC-seq read MultiQC's raw data with the same rules,
and each used to carry its own copy of these. The rules are the templates' own:
numeric text (with thousands separators) counts as a number, the first alias
holding a number wins, and the raw FastQC section is the one with the most reads.

They are NOT the generic registry's rules (``multiqc_registry``), which refuse
numeric text and non-finite values, read one exact column, fold section ids and
break ties differently. Which of those a QC number should follow is an open
decision; ``tests/test_qc_template_helpers.py`` pins both until it is made.
"""

from __future__ import annotations

from collections.abc import Mapping


def number(value: object) -> float | None:
    """A number, or numeric text such as ``"1,234"``; None for anything else, booleans included."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def first_number(sample: Mapping[str, object], *keys: str) -> float | None:
    """First numeric value among the given keys for one sample record."""
    for k in keys:
        if k in sample:
            n = number(sample[k])
            if n is not None:
                return n
    return None


def numbers(section: Mapping[str, object], *keys: str) -> list[float]:
    """One value per sample record in a section, from the first key that holds a number."""
    out: list[float] = []
    for v in section.values():
        if isinstance(v, dict):
            n = first_number(v, *keys)
            if n is not None:
                out.append(n)
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def pick_raw_fastqc(raw: Mapping[str, object]) -> Mapping[str, object]:
    """The RAW FastQC section, chosen by the physical invariant rather than by its suffix.

    nf-core runs FastQC on raw and trimmed reads, and MultiQC stores them as
    ``multiqc_fastqc`` / ``multiqc_fastqc_1`` in no guaranteed order. Trimming
    only removes reads, so the raw section is the one with the higher total read
    count; read depth is a pre-trim quantity. On equal totals the first section
    is kept.
    """
    sections = [v for k, v in raw.items() if isinstance(v, dict) and k.startswith("multiqc_fastqc")]
    best: Mapping[str, object] = {}
    best_total = -1.0
    for sec in sections:
        total = sum(numbers(sec, "Total Sequences", "total_sequences"))
        if total > best_total:
            best_total = total
            best = sec
    return best
