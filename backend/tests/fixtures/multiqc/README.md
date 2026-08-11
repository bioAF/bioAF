# Real MultiQC fixtures

Captured 2026-08-10 from the demo instance's results bucket
(`gs://bioaf-results-bioaf-co-4bd459`). These are the actual reports the QC
extractors were debugged against, kept so parser behavior can be locked against
real output instead of hand-built shapes.

| Fixture | Run | Pipeline | MultiQC | Report path in the bucket |
|---|---|---|---|---|
| `bulk_rnaseq_run17.json` | exp 12 / run 17 | nf-core/rnaseq | 1.19 | `multiqc/star_salmon/multiqc_report_data/` |
| `chipseq_run22.json` | exp 13 / run 22 | nf-core/chipseq | 1.23 | `multiqc/broad_peak/multiqc_data/` |
| `atacseq_run24.json` | exp 14 / run 24 | nf-core/atacseq | 1.13 | `multiqc/broad_peak/multiqc_data/` |
| `scrnaseq_run11.json` | exp 3 / run 11 | STAR + FastQC | 1.31 | `multiqc/multiqc_data/` |

Four MultiQC majors are represented on purpose. The report structure is not
stable across them: `report_general_stats_data` is a **list** of per-sample
dicts up to 1.23 and a **dict keyed by module** from 1.31. Any parser that
walks it has to handle both.

## What was stripped

`report_plot_data` was removed from every fixture (it is 70-90% of the file size
and no metric parser reads it). Everything a metric parser touches is intact:
`report_saved_raw_data`, `report_general_stats_data`,
`report_general_stats_headers`, `report_data_sources`, `config_version`.

Consequence: these fixtures cannot exercise chart-data extraction
(`read_multiqc_chart_data`), which reads `report_plot_data`. Use a synthetic
fixture for that.

## Note on section naming

The same logical module appears under several section ids across pipelines and
versions, which is why module-id normalization exists:

- stage repeats: `multiqc_samtools_flagstat`, `..._1`, `..._2`
- raw vs trimmed: `multiqc_fastqc`, `multiqc_fastqc_1`
- library-level infixes: `multiqc_peak_count-plot` (chipseq) vs
  `multiqc_mlib_peak_count-plot` (atacseq)
- picard instance suffixes: `multiqc_picard_insertSize` vs
  `multiqc_picard-1_insertSize`

Wide sections in these files are distributions, not metric tables, and exist
here deliberately as guard cases: `preseq` (10,000 columns),
`multiqc_dupradar-plot` (1,406), `deeptools_plot_profile_mlib_deeptools` (700),
`multiqc_samtools_idxstats` (196 contigs).
