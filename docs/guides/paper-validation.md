# Paper Validation

Paper Validation reproduces a published paper's analysis on your own infrastructure and tells you
how far the result agrees. You give it a DOI, it reads the methods, picks an nf-core pipeline,
fetches the deposited data, runs it, and compares what it got against what the paper reported.

Nothing runs until you approve it. The plan is shown first, and approval is the point where compute
is spent.

## The four levels

A validation gets as far as the paper and the pipeline allow. Each level is a real result, and each
one means something narrower than the one after it.

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
