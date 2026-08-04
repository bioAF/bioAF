"""Upload helpers for figures and checkpoints."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("bioaf")


def upload_figure(figure: Any, api_post_fn: Callable) -> int | None:
    """Upload a figure and return the file_id.

    Accepts: matplotlib Figure, str/Path to file, or bytes.
    Returns file_id from the API, or None if upload fails.
    """
    try:
        file_bytes: bytes
        filename = "snapshot_figure.png"

        if isinstance(figure, (str, Path)):
            path = Path(figure)
            file_bytes = path.read_bytes()
            filename = path.name
        elif isinstance(figure, bytes):
            file_bytes = figure
        elif hasattr(figure, "savefig"):
            # matplotlib Figure
            buf = io.BytesIO()
            figure.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            file_bytes = buf.read()
        else:
            logger.warning("Unsupported figure type: %s", type(figure))
            return None

        resp = api_post_fn(
            "/api/files/upload/simple",
            files={"file": (filename, file_bytes, "image/png")},
        )
        return resp.get("id")
    except Exception:
        logger.warning("Figure upload failed", exc_info=True)
        return None


def upload_checkpoint(adata: Any, api_post_fn: Callable) -> int | None:
    """Upload obs + obsm checkpoint as parquet.

    Returns file_id from the API, or None if upload fails.
    """
    try:
        import pandas  # noqa: F401  (needed for DataFrame.to_parquet)

        # Start with obs DataFrame
        df = adata.obs.copy()

        # Add obsm arrays as prefixed columns
        if hasattr(adata, "obsm") and adata.obsm is not None:
            for key in adata.obsm.keys():
                try:
                    arr = adata.obsm[key]
                    # Handle sparse matrices
                    if hasattr(arr, "toarray"):
                        arr = arr.toarray()
                    if hasattr(arr, "shape") and len(arr.shape) == 2:
                        for i in range(arr.shape[1]):
                            df[f"{key}_{i}"] = arr[:, i]
                except Exception:
                    logger.warning("Could not add obsm key %s to checkpoint", key)
                    continue

        # Write to parquet buffer
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow")
        buf.seek(0)

        resp = api_post_fn(
            "/api/files/upload/simple",
            files={"file": ("snapshot_checkpoint.parquet", buf.read(), "application/octet-stream")},
        )
        return resp.get("id")
    except Exception:
        logger.warning("Checkpoint upload failed", exc_info=True)
        return None
