"""QC template registry.

Each template is a module exposing `render_config()` and `compute_quality(metrics)`.
The dashboard service dispatches on a pipeline's `qc_template` string into this map.
"""

from __future__ import annotations

from types import ModuleType

from app.services.qc.templates import bulk_rnaseq, custom, scrnaseq

TEMPLATES: dict[str, ModuleType] = {
    "scrnaseq": scrnaseq,
    "bulk_rnaseq": bulk_rnaseq,
    "custom": custom,
}

DEFAULT_TEMPLATE = "scrnaseq"


def get_template(name: str | None) -> ModuleType:
    if not name:
        return TEMPLATES[DEFAULT_TEMPLATE]
    return TEMPLATES.get(name, TEMPLATES[DEFAULT_TEMPLATE])


__all__ = ["TEMPLATES", "DEFAULT_TEMPLATE", "get_template"]
