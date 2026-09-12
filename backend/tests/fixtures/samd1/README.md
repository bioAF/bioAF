# SAMD1 fixture (study 37)

A regression fixture for bioAF's own defects, never a specification. No identifier, filename, column
name, count, cutoff or sample label from it may reach production behaviour.

## Source

- Paper: SAMD1, `10.1126/sciadv.abf2229`. GEO `GSE144396`, a combined series (SAMD1, IgG and histone
  ChIP-seq, plus RNA-seq). The paper states mm9.
- Study 37 on the demo (`bioaf-495400`), requested by DOI on 2026-09-11 with `intended_route=deposit`,
  run on build `8d22a56e`. Exported from the demo as the study's JSON report on 2026-09-11.

## What is here

- `study_37_persisted.json`: the study's state and classification, its reproduction plan and
  comparison targets, and the parts of its evidence the deposit route wrote (`deposit`,
  `deposit_failed`, `deposit_inventory`, `deposit_selection`, `deposit_inspection`,
  `deposit_metadata_association`, `completion`, `discovery_unresolved`, `acquisition_attempts`,
  `capabilities`, `supplements`, `assessment`, `pmcid`, `route`).
- `normalized_counts_header.tsv`: the header row of `GSE144396_RNA-Seq_NormalizedCounts.txt.gz` as
  study 37's inspection recorded it (an unnamed index column, then `WT-1` .. `WT-4`, `KO Cl5 repl1`,
  `KO Cl5 repl2`, `KO Cl16`, `KO Cl33`).

## Bounds

- No paper text: the kept passages, the reconciliation revisions and the pre-compute checks' reasoning
  are left out.
- No people: requester and approver are left out.
- No deposited data: tests that need matrix values synthesize them under the real header, and say so.

## What is not here yet

The owner's manual author-table checks (2026-09-11: 257 up and 524 down at P < 0.01 for the ES cell
contrast; 5,904 significant and 1,904 more than twofold for day-7 differentiation) seed stage 3's
consistency route. Before they are seeded this file records, per plan_7_4 section 3.1, the source file
for each table, the significance definition applied, the owner's reading of the headerless
differentiation table (which column was read as which role, and why), and each table's effect scale
and ratio orientation with the evidence that establishes them.

## Values that must never reach production

`test_prompt_fixture_guard.py` renders every lit_validation prompt and fails if any value below appears
in one (change_7.5 section 1.6). Identifiers, filenames, counts and labels from this fixture; add to the
list whenever the fixture gains one.

- `10.1126/sciadv.abf2229`
- `PMC8115922`
- `GSE144396`
- `PXD016781`
- `6LUI`
- `6LUJ`
- `6LUK`
- `STI20A`
- `GSE144396_RNA-Seq_NormalizedCounts.txt.gz`
- `GSE144396_RNA-Seq_DeSeq2.txt.gz`
- `GSE144396_DeSeq2-Differentiation-SAMD1KOvsWT.txt.gz`
- `SAMD1`
- `L3MBTL3`
- `samd1_chip_peaks`
- `8733`
- `576`
- `257`
- `524`
- `5904`
- `1904`
- `KO Cl5 repl1`
- `KO Cl5 repl2`
- `KO Cl16`
- `KO Cl33`
- `WT-1`
- `WT-2`
- `WT-3`
- `WT-4`

## Owner-owed input for the consistency acceptance (change_7.5 section 4.1)

The consistency route is built and tested on synthetic tables. Its SAMD1 acceptance is seeded from the
owner's manual checks (2026-09-11), and the plan requires this file to record the following BEFORE the
seeding. None of it may be inferred by bioAF or filled in by an agent; each item is the owner's to state.

| Item | Undifferentiated table | Differentiation table |
|---|---|---|
| Source file | owed | owed |
| Significance definition applied | owed | owed |
| Column roles (the differentiation table is headerless), and why | not applicable | owed |
| Effect scale, with the evidence that establishes it | owed | owed |
| Ratio orientation, with the evidence that establishes it | owed | owed |

Until these are recorded, a directional check against either table is `unresolved` with both
readings shown (the tables' headers do not name the ratio), and the headerless table is `unresolved`
with its candidate column roles. A recorded confirmation is passed to the consistency route as an
`interpretation` (`validation_author_consistency.check_claim`) and is reported in `assumptions`.
