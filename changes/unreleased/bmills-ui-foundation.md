### Quality of life improvements

- **A failure now says so.** Across the app, a request that failed used to render
  as an empty list, a zero, or a confident wrong answer. Failed loads state what
  went wrong in plain language and offer a retry, dashboard widgets no longer
  report an outage as a number, and a form whose settings could not be read will
  not let you save component defaults over them. A run list that could not load
  no longer reads "No pipeline runs".

- **Cost history is recorded.** The billing sync ran on every Cost Center page
  load and discarded everything it wrote, so `cost_records` was permanently
  empty and the dashboard's spend trend had nothing to show. The sync now
  commits, runs shortly after startup rather than a day later, and is serialised
  per organisation so two overlapping syncs cannot double a day's spend.

- **Destructive actions confirm in proportion to what they destroy.** Every
  confirmation names what will be deleted and what survives, the strongest one
  requires a typed phrase, and the browser's own `confirm`, `alert` and `prompt`
  are gone from the product in favour of in-app dialogs that can be read,
  cancelled and styled.

- **The app can be driven from the keyboard.** A visible focus ring throughout,
  Escape closes any dialog that closes on a backdrop click, focus is trapped
  inside dialogs and returned to the control that opened them, 38 mouse-only
  controls gained a keyboard path, upload drop zones are reachable without a
  mouse, and 291 table headers are associated with the cells they label for
  screen readers.

- **Pipeline runs sort and page over the whole list.** Sorting is done by the
  server rather than over the rows already on screen, and the page size is
  selectable. The fleet view refreshes without a reload, an in-flight run's
  duration ticks, and a run that failed before Nextflow wrote a trace now shows
  the reason it failed instead of nothing.

- **Notification preferences are honoured.** In-app notifications respect the
  toggle that claimed to control them, email delivery is opt-in and routed
  through the configured SMTP server rather than requiring a rule no screen
  could create, and a partial save no longer wipes stored settings.

- **Smaller frictions.** Search no longer fires a request on every keystroke,
  launching a pipeline keeps the experiment you arrived with, the Plot Archive
  stops indexing report furniture as if it were a plot, and the boot screen
  distinguishes "still starting" from "failed to start".

### User interface improvements

- **Dark mode.** A real theme rather than a shim: colours resolve through
  semantic tokens, the saved theme is applied before first paint so there is no
  flash of the wrong one, and 444 tinted panels that painted light on a dark
  page are fixed.

- **One shell for every page.** The sidebar and header are mounted once by the
  layout instead of being re-declared by each page, which also fixes the
  loading splash, the auth redirect and the nav state drifting between screens.

- **Shared button, card and dialog.** The app has a `Button`, a `Card` and a
  `Modal`. 130 hand-spelled primary and danger actions, 74 hand-spelled panels
  and every hand-rolled dialog overlay now use them, so the same control is not
  re-invented per page. Buttons gained a focus ring and an explicit type, which
  stops a control that only opened a picker from submitting the form around it.

- **Navigation.** Literature and Validation Studies moved into Lab Knowledge,
  every nav label is now the heading of the page it opens, breadcrumbs appear
  across the app, and the sidebar has section icons and a collapsed icon rail.

- **The browser tab names the page.** Every route reported "bioAF", so tabs,
  history entries and bookmarks could not be told apart. Each page now names
  itself, on a full load and on in-app navigation.

- **Every page says what it is for** before it has any data to show, and a slow
  load shows the shape of what is coming rather than a spinner.

- **The Cost / spend trend chart can be read.** Each bar shows its date and
  total on hover or with the arrow keys, and the day of the month sits under the
  bar. The same figures are available to a screen reader as text.

- **A phone can reach the whole table.** Tables that were clipped on narrow
  screens scroll to their remaining columns, the layout uses the full width, and
  the header search is a usable control rather than a 26px sliver.

- **Consistency.** Status colours come from one place instead of per-page maps,
  a missing value renders as one placeholder defined once, and the terminology
  is unified on Templates, Provenance and Workbench Images.

### Validation studies and literature

- **Pick the samples a reproduction runs on.** The Level-3 gate now shows a
  sample manifest resolved from the study's accessions, with test and reference
  groups pre-grouped from the design, manual add, and a free-text fallback when
  the manifest cannot be retrieved.

- **A study that did not get all its samples stops instead of guessing.** Picked
  accessions are resolved to real sample identifiers after fetching, and a study
  whose samples are incomplete parks in a `samples_mismatch` state having spent
  no compute. The detail page names the samples that were not fetched and offers
  either running with what was retrieved or stopping.

- **Studies are named after the paper they reproduce** rather than by number,
  on the list, the detail header and the breadcrumb, with the numeric id kept as
  a secondary qualifier.

- **AI Lit Review reports progress** per source while it runs, with elapsed time
  and a control to stop watching, and library papers are searchable from the
  header.
