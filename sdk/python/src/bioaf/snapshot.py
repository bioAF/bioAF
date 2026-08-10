"""Main snapshot function."""

from __future__ import annotations

import logging
from typing import Any

from bioaf.client import _config, _post

logger = logging.getLogger("bioaf")


def snapshot(
    adata: Any,
    label: str,
    notes: str | None = None,
    figure: Any = None,
    save_checkpoint: bool = False,
    experiment_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    """Capture an analysis snapshot and send to bioAF.

    Args:
        adata: AnnData object (or any object, metadata extraction is best-effort)
        label: Human-readable label for this snapshot
        notes: Optional free-text notes
        figure: Optional figure to attach (matplotlib Figure, file path str/Path, or bytes)
        save_checkpoint: If True, save adata.obs + adata.obsm as parquet checkpoint
        experiment_id: Override default from BIOAF_EXPERIMENT_ID
        project_id: Override default from BIOAF_PROJECT_ID

    Returns:
        Created snapshot response dict
    """
    # Try AnnData extractor
    metadata: dict[str, Any] = {"object_type": "anndata"}
    try:
        from bioaf.extractors.anndata import extract_anndata_metadata

        metadata = extract_anndata_metadata(adata)
    except Exception:
        logger.debug("AnnData extraction failed, using minimal payload", exc_info=True)

    # Build payload
    payload: dict[str, Any] = {
        "label": label,
        "object_type": metadata.get("object_type", "anndata"),
        "cell_count": metadata.get("cell_count"),
        "gene_count": metadata.get("gene_count"),
        "parameters_json": metadata.get("parameters_json"),
        "embeddings_json": metadata.get("embeddings_json"),
        "clusterings_json": metadata.get("clusterings_json"),
        "layers_json": metadata.get("layers_json"),
        "metadata_columns_json": metadata.get("metadata_columns_json"),
    }

    if notes:
        payload["notes"] = notes

    # Resolve experiment/project IDs
    exp_id = experiment_id or (_config.get("experiment_id") and int(_config["experiment_id"]))
    proj_id = project_id or (_config.get("project_id") and int(_config["project_id"]))
    if exp_id:
        payload["experiment_id"] = exp_id
    if proj_id:
        payload["project_id"] = proj_id

    # Session ID from env
    session_id = _config.get("session_id")
    if session_id:
        payload["notebook_session_id"] = int(session_id)

    # Upload figure if provided
    if figure is not None:
        try:
            from bioaf.upload import upload_figure

            file_id = upload_figure(figure, _post)
            if file_id:
                payload["figure_file_id"] = file_id
        except Exception:
            logger.warning("Figure upload failed", exc_info=True)

    # Upload checkpoint if requested
    if save_checkpoint:
        try:
            from bioaf.upload import upload_checkpoint

            file_id = upload_checkpoint(adata, _post)
            if file_id:
                payload["checkpoint_file_id"] = file_id
        except Exception:
            logger.warning("Checkpoint upload failed", exc_info=True)

    return _post("/api/snapshots", json=payload)
