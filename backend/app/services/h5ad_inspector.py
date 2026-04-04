"""Lightweight h5ad file inspector.

Reads HDF5 group metadata from GCS to determine cellxgene compatibility
(requires obsm embeddings like X_umap or X_tsne). Downloads the file to
a temp file for h5py to read -- runs on demand per file, not in bulk.
"""

import logging
import tempfile

import h5py
from google.cloud import storage

logger = logging.getLogger("bioaf.h5ad_inspector")

# Embeddings that cellxgene can use as layouts
KNOWN_EMBEDDINGS = {"X_umap", "X_tsne", "X_pca", "X_draw_graph_fa", "X_diffmap"}


def inspect_h5ad(gcs_uri: str, credentials=None) -> dict:
    """Inspect an h5ad file on GCS and return metadata.

    Returns dict with:
        - embeddings: list of obsm keys (e.g. ["X_umap", "X_pca"])
        - cell_count: number of observations (rows)
        - gene_count: number of variables (columns)
        - cellxgene_ready: bool, True if at least one 2D embedding exists
        - missing: human-readable description of what's missing, or None
    """
    try:
        path = gcs_uri.replace("gs://", "")
        bucket_name = path.split("/")[0]
        blob_path = "/".join(path.split("/")[1:])

        client = storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        with tempfile.NamedTemporaryFile(suffix=".h5ad") as tmp:
            blob.download_to_file(tmp)
            tmp.flush()
            tmp.seek(0)

            with h5py.File(tmp.name, "r") as f:
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
    except Exception as e:
        logger.warning("Failed to inspect h5ad %s: %s", gcs_uri, e)
        return {
            "embeddings": [],
            "cell_count": 0,
            "gene_count": 0,
            "cellxgene_ready": False,
            "missing": "unable to inspect file",
        }
