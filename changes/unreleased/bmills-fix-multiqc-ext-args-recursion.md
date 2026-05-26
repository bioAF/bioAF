### Fixes

- Fix every nf-core pipeline run failing at the MULTIQC step with
  `java.lang.StackOverflowError`. The generated `nextflow.config` set the
  MULTIQC `ext.args` to a self-referential closure
  (`{ (task.ext.args ?: '') + ' --export' }`) that recursed forever when
  Nextflow resolved it. `ext.args` is now a plain `--export` override.
