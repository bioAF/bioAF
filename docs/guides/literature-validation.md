# Literature Validation

Literature validation attempts to validate the findings of a paper enough to determine whether the
paper is worth further review by a human scientist. Its output depends on the LLM model configured
for it and should be treated as informational only.

You give it a DOI. It reads the methods, picks an nf-core pipeline, fetches the deposited data, runs
it, and compares what it got against what the paper reported.

Nothing runs until you approve it. The plan is shown first, and approval is the point where compute
is spent. **That approval is human in both autonomy modes**, because it is where the money goes.

## The output depends on the model, and that is not a caveat

This is an AI feature, and the model reads the paper. It decides which of the paper's numbers are
claims worth checking, and which computed metric each claim corresponds to. A model that cannot hold
a full paper in context, or cannot return dependable structured output, produces a study that scores
nothing, and a study that scores nothing looks exactly like a paper that could not be reproduced.

Two things exist to keep that visible rather than silent:

- **Every decision the model makes is shown on the study**: what it bound each claim to, why, how
  confident it was, and which model decided. If the model declined every claim, the study says
  whether that is because the paper reports nothing bioAF computes, or because the model could not
  map anything. Those are different results and they used to read identically.
- **Settings > Integrations > LLMs warns you** when the model you have chosen is unlikely to manage
  this job, and says why rather than just labelling it. A model bioAF has not assessed is marked
  unassessed, never unsuitable.

Treat the verdict as a triage signal for a human scientist, not as a finding.

## Choosing the model

Literature validation and AI Literature Review are different jobs: one reads a whole paper against a
controlled vocabulary of QC metrics, the other scores relevance over short abstracts. Each can name
its own model under **Settings > Integrations > LLMs**, on any provider you have already configured.
Leave either on the org default and it behaves exactly as it always has.

## The two autonomy modes

One setting, **Settings > Integrations > LLMs > Literature Validation Autonomy**.

| | `assisted` (default) | `autonomous` |
|---|---|---|
| Binding a claim to a metric | the model decides | the model decides |
| A claim the model declines or is unsure of | surfaced on the study for a person | the model must choose, and records low confidence |
| Sample scoping, contrasts, reference build | the model proposes, a person edits | the model decides |
| Approving the plan and the spend | **human** | **human** |
| Ratifying the verdict | a clean `validated` finalises itself; everything else waits for a person | the model accepts or overrides the measured verdict, and says which evidence it reweighed |

**The approval gate is human in both modes.** Autonomy governs the scientific judgment inside a
study, not whether compute gets spent.

In autonomous mode the verdict is still *measured* by deterministic code: the comparison of claimed
numbers against computed ones is rule-based and auditable, and the model does not get to dispute a
computed value. What it can do is judge what those measurements mean, and an override that does not
name the evidence it turns on is discarded.

## The four levels

A validation gets as far as the paper and the pipeline allow, from Level 1 (routing the paper, which
spends nothing) to Level 4 (recovering the paper's actual finding). Each level is a real result, and
each one means something narrower than the one after it.

| Level | What it means | Spends compute |
|---|---|---|
| **1. Route the paper** | The assay is identified from the methods, mapped to a specific nf-core pipeline, and the deposited accession found. Ends with a plan you approve or decline. | No |
| **2. Run the data** | The pipeline runs to completion and produces a QC report. Proves the data is real and usable. | Yes |
| **3. Match the QC numbers** | Read counts, mapping rates, peak counts and similar agree with the paper within tolerance. Proves the processing reproduces. | Yes |
| **4. Reproduce the finding** | The paper's actual claim, such as its differential gene list, is recovered. Requires the paper to have deposited a result table to compare against. | Yes |

A Level 3 result is not a verdict on the paper's science. It says the processing agrees, nothing
more.

## What each mark means

| Mark | Meaning |
|---|---|
| ✓ | Demonstrated on a real published paper |
| - | Supported, but not yet demonstrated on a real paper |
| *(blank)* | Not reached |

**Ceiling** is the highest level an assay can reach at all. It is set by whether bioAF knows which
file that pipeline publishes and how to read it. An assay with a ceiling of 3 will never produce a
Level 4 verdict, and the plan says so before you approve.

## Supported assays

| Assay | Pipeline | 1 | 2 | 3 | 4 | Ceiling |
|---|---|:--:|:--:|:--:|:--:|:--:|
| Bulk RNA-seq | `nf-core/rnaseq` | ✓ | ✓ | ✓ | ✓ | 4 |
| ATAC-seq | `nf-core/atacseq` | ✓ | ✓ | ✓ | ✓ | 4 |
| ChIP-seq | `nf-core/chipseq` | ✓ | ✓ | - | - | 4 |
| Single-cell RNA-seq | `nf-core/scrnaseq` | ✓ | - | - | - | 4 |
| Small RNA-seq | `nf-core/smrnaseq` | ✓ | - | - | - | 4 |
| Amplicon / 16S | `nf-core/ampliseq` | ✓ | | - | - | 4 |
| DNase-seq | `nf-core/atacseq` | - | | | | 4 |
| CUT&RUN / CUT&Tag | `nf-core/cutandrun` | ✓ | | - | | 3 |
| WGBS / RRBS | `nf-core/methylseq` | ✓ | ✓ | - | | 3 |
| Hi-C | `nf-core/hic` | - | | | | 3 |
| CRISPR editing | `nf-core/crisprseq` | - | | | | 3 |
| WGS variant calling | `nf-core/sarek` | - | | | | 3 |
| Shotgun metagenomics | `nf-core/mag` | - | | | | 3 |
| Taxonomic profiling | `nf-core/taxprofiler` | - | | | | 3 |
| MNase-seq | `nf-core/mnaseseq` | - | | | | 3 |
| Differential abundance | `nf-core/differentialabundance` | - | | | | 3 |
| RNA fusion | `nf-core/rnafusion` | - | | | | 3 |
| Alternative splicing | `nf-core/rnasplice` | - | | | | 3 |
| Viral amplicon | `nf-core/viralrecon` | - | | | | 3 |
| Bacterial isolate assembly | `nf-core/bacass` | - | | | | 3 |
| Spatial transcriptomics (Xenium) | `nf-core/spatialaxe` | - | | | | 3 |

**Bulk RNA-seq and ATAC-seq are the only assays fully proven at every level.**

## Known limitations

**Some assays currently pick the wrong pipeline and a few others decline to run altogether.** 
These will prompt the user before running. 
Check the pipeline named on the plan before approving, and decline if it is wrong.

| Assay | Should use | Does today |
|---|---|---|
| Dual RNA-seq | `nf-core/dualrnaseq` | picks `nf-core/rnaseq` |
| Nanopore direct RNA | `nf-core/nanoseq` | picks `nf-core/rnaseq` |
| Single-cell ATAC-seq | a barcode-aware pipeline | picks `nf-core/atacseq`, which is bulk-only |
| Spatial transcriptomics (Visium) | `nf-core/spatialvi` | picks `nf-core/spatialaxe`, which is for Xenium |
| circRNA | `nf-core/circrna` | declines |
| Metatranscriptome | `nf-core/metatdenovo` | declines |
| Iso-Seq | `nf-core/isoseq` | declines |

`nf-core/circrna` and `nf-core/spatialvi` decline or divert because neither has a released version
to install. bioAF will not run a pipeline it cannot pin to a release.

**A pipeline chosen from a family is marked as such.** Where a paper's methods describe the assay
only in words its whole subfield shares ("RNA sequencing"), the plan says the pipeline was weighed
against the alternatives and names the nearest one. That is a prompt to check, not an error. Naming
the accession when you start the study settles it outright, because the deposit records what the
data actually is.

**A paper covering several assays is read as a whole.** bioAF uses the deposited accession's own
declared library type to decide which pipeline to run, so a paper doing both RRBS and RNA-seq runs
the right one for the dataset. The quantitative claims it compares against are not filtered the same
way, so a claim about one assay can be compared against another assay's data. Read the comparison
table before accepting a verdict.

**Only the current genome build of each organism is recognised**: human, mouse, rat, zebrafish,
*C. elegans*, *Drosophila* and *Arabidopsis*. An older build, such as Zv9 or Rnor_6.0, is not mapped
to its modern equivalent, and the run uses a default reference instead.

**Level 4 needs a deposited result table.** Most ChIP-seq papers publish differential binding as
figures rather than tables, so they stop at Level 3 regardless of how well the run went.

**Restricted or withdrawn data stops the study before compute is spent**, and says which samples
were unavailable.
