# Paper Validation

This is a beta feature. **Lab Knowledge > Validation Studies** is shown only when the `lit_validation`
beta flag and required permissions are enabled. Check the plan and evidence for the specific study;
the assay demonstration table below records prior validation, not a new verification on every release.

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

## Review the Plan and Evidence

Before approving a study, review the selected assay, deposited accession, pipeline version, reference
and parameters. The pipeline should match the data you intend to validate. bioAF pins the pipeline to
a released version before running it.

**A pipeline chosen from a family is marked as such.** Where a paper's methods describe the assay
only in words its whole subfield shares ("RNA sequencing"), the plan says the pipeline was weighed
against the alternatives and names the nearest one. That is a prompt to check, not an error. Naming
the accession when you start the study settles it outright, because the deposit records what the
data actually is.

**For a paper covering several assays, select the dataset for the assay you want to validate.**
Review the comparison table against the claims for that dataset before accepting a verdict.

**Reference inference is limited to the builds and aliases implemented for the selected workflow.**
Inspect the actual reference and parameters in the plan/run, especially for an older assembly or a
Paper that omits its reference. Defaults and aliases are not evidence that two genome builds are equivalent.

**Level 4 needs a deposited result table.** Most ChIP-seq papers publish differential binding as
figures rather than tables, so they stop at Level 3 regardless of how well the run went.

**Restricted or withdrawn data stops the study before compute is spent**, and says which samples
were unavailable.
