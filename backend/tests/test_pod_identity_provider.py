"""Unit tests for the PodIdentity seam (Stage 4c).

DB-free: the GKE provider's Workload-Identity annotation mapping (the
``iam.gke.io/gcp-service-account`` KSA annotation), the empty-identity / empty-dict
contract, the no-op ``associate`` hook, and the factory defaulting to GKE on a
GCP/unconfigured install.
"""

import pytest

from app.adapters.pod_identity import (
    DEFAULT_POD_IDENTITY_BACKEND,
    VALID_POD_IDENTITY_BACKENDS,
    create_pod_identity_provider,
    get_pod_identity_provider,
)
from app.adapters.pod_identity.gcp import GkePodIdentityProvider


def test_gke_provider_maps_identity_to_workload_identity_annotation():
    p = GkePodIdentityProvider()
    assert p.pod_identity_annotations("runner@proj.iam.gserviceaccount.com") == {
        "iam.gke.io/gcp-service-account": "runner@proj.iam.gserviceaccount.com"
    }


def test_gke_provider_returns_empty_dict_for_empty_identity():
    # No GSA email -> no binding annotation (pod gets no GCP identity), matching
    # the historical ``if gcp_sa_email:`` guard on every KSA create/patch path.
    p = GkePodIdentityProvider()
    assert p.pod_identity_annotations("") == {}
    assert p.pod_identity_annotations(None) == {}


def test_gke_associate_is_a_noop():
    # GCP binds via the KSA annotation, so there is no out-of-band association
    # call (that hook exists for EKS Pod Identity). It must be a harmless no-op.
    p = GkePodIdentityProvider()
    assert p.associate("runner@proj.iam.gserviceaccount.com", "bioaf-pipelines", "bioaf-pipeline-runner") is None


def test_factory_defaults_to_gke():
    assert DEFAULT_POD_IDENTITY_BACKEND == "gke"
    assert "gke" in VALID_POD_IDENTITY_BACKENDS
    assert isinstance(create_pod_identity_provider("gke"), GkePodIdentityProvider)


def test_get_pod_identity_provider_falls_back_to_gke_when_cache_unloaded():
    # backend_for('pod_identity') falls back to the gcp policy default when the
    # resolved-backend cache is unloaded (tests / pre-DB), so this is GKE.
    from app.platform.cloud_provider import reset_resolved_backends

    reset_resolved_backends()
    assert isinstance(get_pod_identity_provider(), GkePodIdentityProvider)


def test_unknown_backend_raises():
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        create_pod_identity_provider("eks")  # no EKS impl until Stage 6e
