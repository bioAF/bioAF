### Infrastructure

- "Check for Infrastructure Updates" no longer fails its background apply with
  a GCP 400 ("Must specify a field to update"). GKE node pools stop churning on
  a no-op `node_locations` diff, and the additive-only apply now skips a benign
  no-op update instead of aborting the whole batch.
- Recent Operations spells out why a plan run ended instead of showing a bare
  "cancelled": "No changes" when nothing was planned, "Not applied" when changes
  were found but not applied by that run, and the abandon reason in a tooltip for
  user-cancelled runs.
