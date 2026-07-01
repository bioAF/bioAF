### Conversational assistant

- Add an AI assistant you can chat with to run bioinformatics pipelines without writing Nextflow.
  Describe what you have and what you want, and it resolves the right experiment or sample, recommends
  and installs the appropriate nf-core pipeline, sets up experiments and samples, imports data by
  accession, launches the run, and explains the results, all in plain language. It is opened from an
  icon in the header and is available to roles that hold the `assistant:use` permission.
- Every consequential action (installing a pipeline, creating an experiment or sample, launching a run)
  is presented as a plan you confirm before anything runs, and launching shows a "this will spend
  compute" warning. The assistant only ever acts within your own role's permissions, and content it
  reads back (sample metadata, imported accession data, QC text) is treated as data, never as
  instructions.
- Read back a run's QC metrics and get a plain-language explanation of what the results mean, in chat.
  Users who hold the AI review permission can also ask the assistant to run a full, saved agent review
  of a run.
- Ask "what did I run this session?" to get a log of the actions taken in the conversation.
- Conversations are saved: reopen and resume any past chat from the assistant's History.

### Audit

- Actions taken through the assistant are attributed to you in the audit log and marked "via assistant",
  so a reviewer sees who took each action and that the agent was used.

### Fixes

- Tear down cellxgene publishes that never become ready and surface the teardown failure instead of
  leaking the resources.
