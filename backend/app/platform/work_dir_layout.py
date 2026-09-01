"""Where a pipeline run's Nextflow work directory lives.

Shared by the compute adapter that creates it and the reaper that deletes it. It
sits in `app.platform` because both layers need it and an adapter importing from
`app.services` is the inversion `test_bal_layering` pins.

Per-run scoping is the whole point. A single shared directory accumulated 2.13 TB
across five runs, and nothing could tell one run's garbage from another's live
intermediates, so none of it could be deleted safely.
"""

from __future__ import annotations

# Root prefix under the raw bucket. Keys only: minting a URI from a key is the
# storage adapter's job, so no scheme appears here.
WORK_DIR_ROOT = "nextflow-work"


def work_dir_key(run_id: int) -> str:
    """The storage key of one run's work directory."""
    return f"{WORK_DIR_ROOT}/run-{run_id}"
