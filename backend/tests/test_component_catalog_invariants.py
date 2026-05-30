"""Invariants that guard against component-catalog drift.

Three places in the codebase name components with different keys and that
drift has caused silently-broken status updates in the past. These tests pin
the contract: every key any runtime path writes to `component_states` must
exist in `COMPONENT_CATALOG`. The catalog is the source of truth.
"""

from app.api.stack_deploy import (
    _CELLXGENE_COMPONENTS,
    _NOTEBOOK_COMPONENTS,
    KUBERNETES_COMPONENTS,
)
from app.services.component_service import COMPONENT_CATALOG


def test_notebook_image_target_keys_exist_in_catalog():
    """Image services issue UPDATE component_states WHERE component_key IN (...).
    Those keys must exist in the catalog or the status flip is a silent no-op.
    """
    missing = [k for k in _NOTEBOOK_COMPONENTS if k not in COMPONENT_CATALOG]
    assert missing == [], (
        f"_NOTEBOOK_COMPONENTS targets keys not in COMPONENT_CATALOG: {missing}. "
        f"This means notebook image build success/failure cannot flip the "
        f"status of these components, and the UI will be stuck on 'provisioning'."
    )


def test_cellxgene_image_target_keys_exist_in_catalog():
    """Same invariant for the cellxgene image service."""
    missing = [k for k in _CELLXGENE_COMPONENTS if k not in COMPONENT_CATALOG]
    assert missing == [], (
        f"_CELLXGENE_COMPONENTS targets keys not in COMPONENT_CATALOG: {missing}."
    )


def test_kubernetes_components_keys_exist_in_catalog():
    """The toggle endpoint accepts any key in KUBERNETES_COMPONENTS. Those
    keys must exist in the catalog so dependency checks and status reads
    resolve.
    """
    catalog_keys = set(COMPONENT_CATALOG.keys())
    missing = [c["key"] for c in KUBERNETES_COMPONENTS if c["key"] not in catalog_keys]
    assert missing == [], (
        f"KUBERNETES_COMPONENTS exposes keys not in COMPONENT_CATALOG: {missing}. "
        f"Toggling these components writes to component_states rows that have "
        f"no catalog entry, which breaks the dependency graph and the components UI."
    )
