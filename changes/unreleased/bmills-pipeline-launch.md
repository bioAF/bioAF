### Pipeline runs

- When bioAF cannot confirm that the compute cluster accepted a pipeline run, the run is now kept and marked failed, with a plain message saying the job may not have started or may still be running on the cluster. Previously the launch returned an error and left no run to inspect. The technical error is in the server logs under the run's number. Such a run is not reconciled with the cluster automatically.
- The experiment moves to processing only when the cluster accepted its run.
- The assistant reports a run that could not be confirmed as "may not have started" instead of announcing it as launched. An automatic run that fails this way is cancelled with the reason `launch_failed`.
- Run started, run failed, automatic run, and quota warning notifications are sent only after the launch is saved, so an abandoned launch no longer announces a run that does not exist.
- A launch refused for exceeding quota still records the refusal in the audit log.

### Reproducing runs

- Reproducing a catalog run now relaunches what the original ran: its recorded parameters, per-sample values, reference genome label, alignment algorithm, project, and its choice to use derived inputs. Defaults added to the catalog since the original ran are not applied.
- A catalog run is reproduced only when it would be given exactly the samplesheet it was given before. Confirming that samples without files should be dropped allows rows to be removed, but never changed or added. Otherwise reproduction is refused before anything is created, with a message saying why.
- Reproduction is refused for a run made on a different pipeline version than the one installed, or when either version is unknown. It is also refused for a run recorded before bioAF kept its samplesheet, per-sample values, and parameters.
- Reproducing a custom pipeline run now relaunches it through the custom pipeline launcher, on the version, variables, inputs, experiment, and project it originally used. This requires permission to launch custom pipelines. Reproduction is refused if that version is deprecated, its environment is not ready, or any of its inputs has since been deleted.

### Fixes

- A run in another organization can no longer be reproduced by its ID; it is reported as not found.
- Launching a custom pipeline version through another pipeline's address is refused before a run is created.
