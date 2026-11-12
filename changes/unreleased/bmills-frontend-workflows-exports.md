### Data exports

- Experiment and project exports behave the same way. Closing the export
  dialog, or moving to another experiment or project, stops waiting for any
  export still in progress: a late answer no longer appears in the next
  dialog, and a download you cancelled is not saved.
- Get Download Link is available only when your storage can make download
  links. Otherwise it stays visible but disabled, with the reason, and the
  advice for exports over 1 GB no longer recommends it. Download Now always
  works.
- A download link shows how long it lasts only when the server says so; it no
  longer always claims 24 hours. A missing or unusable link is reported as a
  failure instead of "Download started".
- If copying the link fails, the dialog says so and the link stays on screen.
  A failed export can be retried with Back.

### Experiments

- Moving from one experiment to another no longer carries over the previous
  experiment's samples, edits, selection or errors.
- If the experiment cannot be refreshed after a change, the page keeps what it
  showed and says it may be out of date.

### Notebooks and work nodes

- Choosing a different experiment or workbench template while the previous
  choice is still loading no longer shows the earlier files or version.
- Launching sends one request however fast Launch or "Launch anyway" is
  pressed.
- A work node that is still provisioning no longer blocks launching another.
  Each launch keeps its own progress or error message.
- A recreated session or work node keeps its saved template version.
- The notebook image (used by JupyterHub and RStudio) builds again. Every build
  since 2026-08-31 failed about 40 minutes in, while checking the single-cell
  file reader, so JupyterHub and RStudio showed "build failed".

### Custom pipelines

- An unsaved new version or name edit is no longer lost when the page reloads
  after deprecating a version or saving the name.
- Create Version sends one request however fast it is pressed.

### Setup

- Verify, Create Admin Account and Save & Validate each send one request
  however fast they are pressed.
- Settings filled in from the installer no longer overwrite a project ID,
  organization slug or role you typed while they were loading.

### Infrastructure

- Infrastructure actions now require the matching permission. Teardown,
  destroying storage, component and cluster changes, and cancelling an image
  build need infrastructure configure. Deploying, checking or applying
  infrastructure updates, deploying or updating storage, cleaning up or
  dismissing an orphaned resource, and recovering a deployment need
  infrastructure deploy. Initializing infrastructure and abandoning a running
  operation need infrastructure create. Auto-ingest settings need pipelines
  configure. Buttons you cannot use stay visible, disabled, with the reason.
- Cleaning up or dismissing a single orphaned resource, and deploying or
  updating storage, now require infrastructure deploy on the server as well.
  They previously needed only view access.
- If the infrastructure status cannot be read, the Components page says so
  with Retry instead of offering to deploy.
- Recent operations and the cluster configuration each say when they could
  not be loaded, with Retry. A failed refresh keeps what was shown and says
  it may be out of date, and retrying the cluster configuration keeps your
  unapplied changes.
