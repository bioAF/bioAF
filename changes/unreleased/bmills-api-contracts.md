### Sample import

- Importing samples from a CSV or a Google Sheet no longer attaches custom
  field values to the wrong samples when an earlier row is rejected.
- A row that conflicts with an existing sample (a duplicate sample ID or a
  taken sequencing-batch position) is now reported as a row error, and the
  other rows still import. Before, one conflict failed the whole import with a
  server error and nothing was saved.
- A rejected row no longer leaves anything behind: no partial sample, custom
  field, new batch or audit entry.
- A custom field name longer than 255 characters is now reported as a row error
  instead of failing the whole import.
- Google Sheet sample preview and import now answer "Experiment not found" for
  an experiment that is missing or outside your organization, before the sheet
  is read.

### Notebooks

- Notebook session details, stop, sync and provenance now refuse sessions that
  belong to another organization.
- An API key without the user-management scope sees and stops only its own
  notebook sessions, even when its role could manage everyone's.

### Assistant

- When a confirmed assistant action fails with a specific reason (for example a
  conflict), the error now keeps that reason's status instead of a generic
  "action failed" 400.
