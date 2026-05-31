### Setup wizard

- Add a "Select Components" step at the end of the wizard. After the user
  picks Kubernetes and kicks off the stack deploy, the wizard surfaces the
  same component grid as the post-install Infrastructure > Components page
  with `nextflow` and `jupyterhub` pre-checked. Selections are queued
  immediately, and the new "Deploying" step shows live per-component status
  (queued, building, ready, failed) so the user can leave with confidence
  that the system will be ready for their first experiment and pipeline
  without having to re-open the Infrastructure menu.
- Add a "Back" link on every step from Create Admin through Select Stack,
  preserving previously entered field values. Back is hidden once the
  Terraform deploy has been triggered.
- Re-submitting an already-completed wizard step no longer fails. If the
  user goes back to a finished step and clicks Continue with unchanged
  values, the wizard advances without re-calling the backend. If the user
  edits a value, the wizard shows the diff and asks to confirm the
  overwrite. A "Forward" link appears alongside Back on completed steps so
  the user can go back to verify a value (e.g. "which email did I use for
  admin?") and forward again without re-submitting. The Create Admin
  backend endpoint is now idempotent: a second call with the same setup
  token updates the existing admin instead of returning 409.
- Widen the setup card so the 10-step indicator has breathing room.

### Component lifecycle

- Add a `queued_for_infra` component state and a `process_queued_components`
  orchestrator that drains queued components as readiness flips: cluster-only
  components (`nextflow`, `snakemake`, `qc_dashboard`, `meilisearch`) move to
  enabled when the cluster is up; image-bound components (`jupyterhub`,
  `rstudio`, `cellxgene`) kick off Cloud Build as soon as storage is up and
  flip to enabled once both image and cluster are ready. The orchestrator
  runs from `stack/deploy-background`, the storage- and compute-ready hooks
  in `deploy_stack`, and each image poll's SUCCESS branch.
- Add `POST /api/components/select-batch` for the wizard to queue components
  transactionally, validated against the canonical Kubernetes component list.
- Surface `has_in_flight_components` on `GET /api/bootstrap/status` so
  returning users mid-deploy can be routed back to the components view.
- `POST /api/v1/infrastructure/stack/deploy-background` now records
  `compute_stack` in `platform_config` up front so the wizard's component
  fetch resolves to the right list before the compute module finishes.

### Fixes

- Close a drift between `KUBERNETES_COMPONENTS` and `COMPONENT_CATALOG`: the
  toggle endpoint accepted `jupyterhub` while the catalog only had
  `jupyter_k8s`, so the status flip in the image services was a silent no-op
  against a non-existent catalog entry.
- Render `queued_for_infra` as a "Queued" badge on the existing components
  page instead of falling through to the default "Disabled" treatment.
- Drop the dollar-estimate badges from per-component cards in the wizard to
  match the post-install page.
