# Real nf-core samplesheet schemas

Captured 2026-08-11 from `raw.githubusercontent.com/nf-core/<name>/<tag>/assets/schema_input.json`,
each pinned to the **same release tag `NfCoreRegistryService.install` pins** (the
newest non-dev release), so these are the exact contracts a bioAF user installing
that pipeline would receive.

These are real published schemas, not hand-built shapes, for the same reason the
MultiQC fixtures next door are real: the first catalog scan of this work was
wrong until it was measured, and a hand-written schema would have encoded the
assumption instead of testing it.

| Fixture | Tag | Bytes | Why it is here |
|---|---|---|---|
| `sarek.json` | 3.9.0 | 6125 | Class A1. Requires `patient` (`meta: [patient]`), which bioAF holds as `donor_source`. The headline unblock. |
| `bacass.json` | 2.6.0 | 3180 | Class A1. Requires `ID` with `meta: [sample]`: the same concept under a third spelling. |
| `genomeqc.json` | dev | 2323 | Class A1. Requires `species`, and its `meta` is `[id]`, so the identity column is the organism. |
| `taxprofiler.json` | 2.0.1 | 2858 | Class A1 with an `enum`: `instrument_platform` accepts 11 named platforms, so a free-text fill is invalid. |
| `funcscan.json` | 4.0.0 | 2593 | Class B. Requires `fasta` and defines **no** `fastq_1`, so it is not launchable from bioAF samples at all. |
| `mag.json` | 5.5.0 | 3073 | Class A2. `group` (`meta: [group]`) controls co-assembly: filling it from `treatment_condition` silently decides the assembly design. |
| `rnastructurome.json` | dev | 5377 | Class A2, the sharpest case. `condition` is an enum of `treated/untreated/denatured`, an rf-norm chemistry concept, **not** a general treatment condition. |
| `rnasplice.json` | 1.0.4 | 1891 | Class A2 plus an enum trap: `strandedness` accepts `forward/reverse/unstranded` and **not** bioAF's `"auto"` default. |
| `demo.json` | 1.2.0 | 1374 | Control. Already valid today; locks the no-regression case. |

`genomeqc` and `rnastructurome` resolve to `dev` because neither has cut a
release yet. That is what the installer would pin too, so it is the honest
fixture.

## What these are for

Two questions, both answered from the schema rather than from the pipeline name:

1. **Is this pipeline launchable from bioAF samples?** It is when the row object
   defines `fastq_1`. `funcscan` does not, so it is refused with an explanation
   instead of being handed a FASTQ sheet that dies inside Nextflow.
2. **Can bioAF supply every required column?** Identity and provenance columns
   are filled from `Sample` fields. Columns that define experimental design
   (`mag`'s `group`, `rnastructurome`'s `condition`) are never guessed, because a
   wrong value there produces a scientifically wrong result that still runs
   green.

`MANIFEST.json` carries the source URL and byte count for each file, so the
capture can be re-verified without re-deriving the tags.
