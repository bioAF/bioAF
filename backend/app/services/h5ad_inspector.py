"""Lightweight h5ad file inspector.

Reads HDF5 group metadata from storage to determine cellxgene compatibility
(requires obsm embeddings like X_umap or X_tsne). Downloads the file to
a temp file for h5py to read -- runs on demand per file, not in bulk.
"""

import logging
import os
import tempfile

import h5py

logger = logging.getLogger("bioaf.h5ad_inspector")

# Embeddings that cellxgene can use as layouts
KNOWN_EMBEDDINGS = {"X_umap", "X_tsne", "X_pca", "X_draw_graph_fa", "X_diffmap"}


async def inspect_h5ad(gcs_uri: str) -> dict:
    """Inspect an h5ad file in storage and return metadata.

    Returns dict with:
        - embeddings: list of obsm keys (e.g. ["X_umap", "X_pca"])
        - cell_count: number of observations (rows)
        - gene_count: number of variables (columns)
        - cellxgene_ready: bool, True if at least one 2D embedding exists
        - missing: human-readable description of what's missing, or None
    """
    from app.adapters.registry import get_storage_adapter

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".h5ad")
        os.close(fd)
        await get_storage_adapter().download_to_filename(gcs_uri, tmp_path)
        return _inspect_local_h5ad(tmp_path)
    except Exception as e:
        logger.warning("Failed to inspect h5ad %s: %s", gcs_uri, e)
        return {
            "embeddings": [],
            "cell_count": 0,
            "gene_count": 0,
            "cellxgene_ready": False,
            "missing": "unable to inspect file",
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _inspect_local_h5ad(path: str) -> dict:
    """Read HDF5 metadata from a local h5ad file path."""
    with h5py.File(path, "r") as f:
        obsm_keys = list(f["obsm"].keys()) if "obsm" in f else []

        cell_count = 0
        if "X" in f:
            x = f["X"]
            if hasattr(x, "shape"):
                cell_count = x.shape[0]
        if cell_count == 0 and "obs" in f:
            cell_count = f["obs"].attrs.get("_index_length", 0)

        gene_count = 0
        if "X" in f:
            x = f["X"]
            if hasattr(x, "shape") and len(x.shape) > 1:
                gene_count = x.shape[1]
        if gene_count == 0 and "var" in f:
            gene_count = f["var"].attrs.get("_index_length", 0)

    embeddings = [k for k in obsm_keys if k.startswith("X_")]
    has_layout = any(k in KNOWN_EMBEDDINGS for k in embeddings)

    missing_parts = []
    if not has_layout:
        missing_parts.append("embeddings (UMAP/t-SNE)")

    return {
        "embeddings": embeddings,
        "cell_count": cell_count,
        "gene_count": gene_count,
        "cellxgene_ready": has_layout,
        "missing": ", ".join(missing_parts) if missing_parts else None,
    }
