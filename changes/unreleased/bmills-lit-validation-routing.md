### Paper validation

- Route a paper on the assay it actually ran, not on the first family word its
  methods section happens to use. A paper saying "RNA-seq" while describing gene
  fusion detection now plans `nf-core/rnafusion`, alternative splicing plans
  `nf-core/rnasplice`, and viral amplicon sequencing plans `nf-core/viralrecon`.
  Ordinary bulk RNA-seq is unchanged.
- Say on the plan when a pipeline was WEIGHED rather than merely matched, so a
  scientist can see that the whole family was on the table, what the nearest
  alternative was, and that scoping the study to its accession would settle it
  outright.

### Documentation

- Add a Paper Validation guide covering the four levels, which assays are
  verified against which pipeline, and which still route to the wrong one. Linked
  from the docs index.

### Fixes

- Stop capturing metatranscriptomics papers for `nf-core/rnaseq`. An assay
  marker had been matched anywhere inside a word, so `transcriptom` matched
  inside `metatranscriptomics`.
- Stop offering `nf-core/rnaseq` for a circular RNA paper. Where no pipeline can
  be pinned to a released version, the plan now refuses rather than naming a
  pipeline that would answer confidently about the wrong thing.
- Reach `nf-core/bacass` for a bacterial assembly paper, which was previously
  refused as an unbreakable tie.
- Stop a paper that listed its QC tools (samtools, FastQC, MultiQC) from being
  routed on them.
