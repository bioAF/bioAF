"""Where a pipeline run's outputs live, and which URIs belong to that place.

Shared by the compute adapter that writes outputs, the services that collect, ingest and register
them, and the reaper that deletes them. It sits in `app.platform` for the same reason as
`work_dir_layout`: adapters may not import `app.services`. Keys only, plus a membership rule that
takes the storage adapter as an argument; minting and parsing URIs stays the adapter's job.

A run has exactly one output location, derived from its own identity. Nothing a caller supplies
(a launch parameter, a stored `outdir`, a custom-pipeline variable) can name another one.
"""

from __future__ import annotations

from collections.abc import Mapping


def results_bucket_from_config(cfg: Mapping[str, str | None]) -> str | None:
    """The results bucket: ``results_bucket_name``, else derived from a ``bioaf-raw-`` raw bucket.

    Installs populate the raw bucket before the results bucket, so the derivation is the same one
    the compute adapter and the QC extractors have always used. None when neither yields a bucket.
    """
    results = cfg.get("results_bucket_name")
    if results and results != "null":
        return results
    raw = cfg.get("raw_bucket_name") or ""
    if raw.startswith("bioaf-raw-"):
        return raw.replace("bioaf-raw-", "bioaf-results-", 1)
    return None


def run_output_key(*, run_id: int | None, experiment_id: int | None, project_id: int | None = None) -> str:
    """The storage key of one run's output directory (no trailing separator).

    Experiment runs (every catalog run, and experiment-scoped custom pipelines) live under their
    experiment; a project-scoped custom pipeline run under its project, which is where its EXIT
    trap has always written.
    """
    if not run_id or run_id < 0:
        raise ValueError("A run has no output location before it has an id")
    if experiment_id is not None:
        return f"experiments/{experiment_id}/pipeline-runs/{run_id}"
    if project_id is not None:
        return f"projects/{project_id}/pipeline-runs/{run_id}"
    raise ValueError(f"Run {run_id} has neither an experiment nor a project")


def directory_prefix(uri: str) -> str:
    """A directory URI terminated by exactly one separator, for listing its contents.

    Object-store prefixes match literally: ``run-1`` also matches ``run-10``. A directory consumer
    lists ``run-1/``.
    """
    return uri.rstrip("/") + "/"


def _key_segments_safe(key: str) -> bool:
    return all(seg not in ("", ".", "..") for seg in key.split("/"))


def uri_within(storage, uri: str | None, prefix_uri: str) -> bool:
    """Whether ``uri`` names an object inside the directory ``prefix_uri``.

    Both are parsed by ``storage`` (so the scheme must be that backend's), the bucket/store must
    match exactly, the object key must have no empty, ``.`` or ``..`` segment, and the match is on
    a separator boundary. The directory itself is not an object inside it.
    """
    if not uri:
        return False
    try:
        bucket, key = storage.parse_uri(uri)
        prefix_bucket, prefix_key = storage.parse_uri(prefix_uri)
    except Exception:
        return False
    if bucket != prefix_bucket or not _key_segments_safe(key):
        return False
    directory = prefix_key.rstrip("/")
    if not directory or not _key_segments_safe(directory):
        return False
    return key.startswith(directory + "/") and len(key) > len(directory) + 1
