### Pipeline reports

- Pipeline reports, QC summaries and provenance details can no longer run
  scripts in your browser session. Text written by a pipeline or entered as
  metadata is shown as text.
- Markdown reports from custom pipelines show headings, lists, emphasis, code
  and links. Embedded HTML is not shown, images are not loaded (their
  description is shown instead), and only web, email and in-page links are
  clickable.
- HTML reports (Nextflow and custom) run in an isolated frame. Charts, tables,
  tabs and in-report links work as before, but the report cannot read your
  session or change the page around it.
- Markdown reports no longer end with stray script text.
- QC summaries keep their bold values. Anything else in a summary, such as
  link or image syntax, is shown exactly as written.
- The provenance preview shows names, filenames and other details exactly as
  recorded, without turning them into links, images or headings.

### Forms and dialogs

- New Project, Add Sample, Edit Sample, Bulk Edit, Create Sample Batch and
  Add GitHub Repo can be submitted with Enter. A second Enter or click while
  saving no longer creates a duplicate.
- Every field on these forms has a visible label. In Edit Sample, each custom
  field is now labelled with its own name.
- Errors on these forms, and a failed reference upload, are announced to
  screen readers.
- Escape closes only the dialog on top: a confirmation opened from another
  dialog no longer closes both, and a dialog that is still working keeps
  both open.
- Keyboard focus stays inside an open dialog while it is working and after a
  request fails.
- Dismissing a paper, reversing a dismissal and dismissing several papers now
  say when they fail. The dialog keeps what you entered so you can try again.

### Lists, tabs and detail pages

- Projects, experiments and reference data share one loading, empty and error
  presentation. A failed load is never shown as an empty list.
- The experiment and pipeline run lists share the same paging controls, with
  "Showing a–b of n" and the page number.
- The experiment and reference detail tabs can be used with the keyboard: the
  arrow keys, Home and End move between tabs, and Enter or Space opens one. On
  narrow screens the tabs scroll sideways instead of running off the page.
- Work node actions, messages and selection colours now match the rest of the
  app. A failed launch is announced, and a GitHub repo can be added with Enter.

### Fixes

- File sizes are shown the same way as before on every screen. A negative or
  invalid size shows the screen's usual placeholder instead of "NaN
  undefined". Very large and sub-byte sizes no longer show "undefined".
- Records with no creation time show the not-set placeholder instead of
  1 January 1970. An unreadable time no longer shows "Invalid Date".
- A run duration is never negative.
