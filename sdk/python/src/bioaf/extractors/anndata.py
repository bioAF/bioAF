"""AnnData metadata extractor for snapshot storage."""

from __future__ import annotations

from typing import Any


def extract_anndata_metadata(adata: Any) -> dict:
    """Extract metadata from an AnnData object for snapshot storage.

    Handles missing attributes gracefully: returns what's available.
    """
    result: dict[str, Any] = {
        "object_type": "anndata",
        "cell_count": getattr(adata, "n_obs", None),
        "gene_count": getattr(adata, "n_vars", None),
    }

    # Embeddings from obsm
    if hasattr(adata, "obsm") and adata.obsm is not None:
        embeddings: dict[str, dict] = {}
        for key in adata.obsm.keys():
            try:
                val = adata.obsm[key]
                # Handle sparse matrices, numpy arrays, DataFrames
                if hasattr(val, "shape") and len(val.shape) >= 2:
                    embeddings[key] = {"n_components": int(val.shape[1])}
                else:
                    embeddings[key] = {"n_components": None}
            except (IndexError, AttributeError):
                embeddings[key] = {"n_components": None}
        result["embeddings_json"] = embeddings

    # Clusterings from categorical obs columns
    if hasattr(adata, "obs") and adata.obs is not None:
        clusterings: dict[str, dict] = {}
        for col in adata.obs.columns:
            try:
                series = adata.obs[col]
                is_categorical = hasattr(series, "cat") or (
                    hasattr(series, "dtype") and getattr(series.dtype, "name", "") == "category"
                )
                if is_categorical:
                    counts = series.value_counts()
                    clusterings[col] = {
                        "n_clusters": len(counts),
                        "distribution": {str(k): int(v) for k, v in counts.items()},
                    }
            except Exception:
                continue
        result["clusterings_json"] = clusterings
        result["metadata_columns_json"] = list(adata.obs.columns)

    # Parameters from uns
    if hasattr(adata, "uns") and adata.uns is not None:
        params: dict[str, Any] = {}
        for key, value in adata.uns.items():
            try:
                if isinstance(value, dict):
                    if "params" in value:
                        params[key] = {"params": _serialize_params(value["params"])}
                    elif all(isinstance(v, (str, int, float, bool, type(None))) for v in value.values()):
                        params[key] = dict(value)
            except Exception:
                continue
        result["parameters_json"] = params

    # Layers
    if hasattr(adata, "layers") and adata.layers is not None:
        try:
            result["layers_json"] = list(adata.layers.keys())
        except Exception:
            pass

    return result


def _serialize_params(params: Any) -> dict:
    """Convert parameter values to JSON-serializable types."""
    result: dict[str, Any] = {}
    if not isinstance(params, dict):
        return result
    for k, v in params.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            result[k] = v
        elif hasattr(v, "item"):  # numpy scalar
            result[k] = v.item()
        elif isinstance(v, dict):
            result[k] = _serialize_params(v)
        else:
            result[k] = str(v)
    return result
