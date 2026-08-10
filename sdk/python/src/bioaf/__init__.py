"""bioAF SDK: capture analysis snapshots from Jupyter/RStudio notebooks."""

from bioaf.client import connect
from bioaf.snapshot import snapshot

__all__ = ["connect", "snapshot"]
__version__ = "0.1.0"
