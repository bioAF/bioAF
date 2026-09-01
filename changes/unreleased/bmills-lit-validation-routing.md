### Paper validation

- Route a paper on the assay it actually ran, not on the first family word its
  methods section happens to use. A paper saying "RNA-seq" while describing gene
  fusion detection now plans `nf-core/rnafusion`, alternative splicing plans
  `nf-core/rnasplice`, and viral amplicon sequencing plans `nf-core/viralrecon`.
  Ordinary bulk RNA-seq is unchanged.
- Use the tools a paper names as evidence for which pipeline to run. A methods
  section naming Arriba or rMATS says which member of the RNA-seq family it
  belongs to, where the assay string alone does not.
- Weigh a pipeline's declared topics by how many other pipelines declare the
  same ones, so a topic that names one pipeline in the catalog counts for more
  than two that every pipeline in a subfield shares.
- Say on the plan when a pipeline was WEIGHED rather than merely matched, so a
  scientist can see that the whole family was on the table and what the nearest
  alternative was.

### Fixes

- Stop capturing metatranscriptomics papers for `nf-core/rnaseq`. An assay
  marker had been matched anywhere inside a word, so `transcriptom` matched
  inside `metatranscriptomics`.
- Stop offering `nf-core/rnaseq` for a circular RNA paper. Where no pipeline can
  be pinned to a released version, the plan now refuses rather than naming a
  pipeline that would answer confidently about the wrong thing.
- Reach `nf-core/bacass` for a bacterial assembly paper, which was previously
  refused as an unbreakable tie.
