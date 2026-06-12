### Internal

- Added a backend-neutral storage-URI primitive to the storage adapter:
  `build_uri(bucket, key)` mints a URI for an explicit bucket/key pair
  (`gs://` on GCS, `file://` on NFS) and `parse_uri(uri)` is its inverse, both
  with round-trip tests. Migrated every call site that hardcoded
  `f"gs://{bucket}/{key}"` onto it, draining the BAL layering guard's `gs://`
  scheme-literal allowlist from 28 files to 0. Behavior-preserving: the minted
  URI is byte-identical on a GCS install, so there is no change for existing or
  new installs. This removes the last `gs://` literals from the service layer,
  a prerequisite for supporting non-GCS storage backends.

### Fixes

- `./bioaf setup` no longer silently drops flags when it re-execs under
  `sg docker` to activate docker-group membership. Previously only `--version`
  survived the hop, so `--prefill` and `--local-build` were discarded on any
  host whose shell did not yet have an active `docker` group (the common
  fresh-VM case), which left the GCP project/region/zone prefill unapplied and
  the setup wizard blank. The re-exec now preserves the full original argument
  list.
- `./bioaf setup --local-build` now applies the prefill already present on the
  host (`~/.bioaf-prefill.yaml`, then `~/.bioaf/prefill.yaml`) without requiring
  an explicit `--prefill`, so building from local source still pre-populates
  `platform_config`.
