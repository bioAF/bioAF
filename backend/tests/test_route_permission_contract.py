"""The permission contract of every HTTP route, checked against the live route table.

Every route must either declare a permission gate or appear in one of the sets
below, each of which is a deliberate decision. A new route with no gate fails here
until someone decides what it needs. These are structural checks: the behavior
behind them is proved in test_permissions_and_ownership.py.
"""

import pytest

from app.api.dependencies import RESULTS_VIEW_PERMISSIONS
from app.main import app
from app.middleware.auth_middleware import PUBLIC_PATHS

VIEW_EXPERIMENTS = {("experiments", "view")}
VIEW_RESULTS = set(RESULTS_VIEW_PERMISSIONS)
CONFIGURE_INFRASTRUCTURE = {("infrastructure", "configure")}
VIEW_INFRASTRUCTURE = {("infrastructure", "view")}

# Reachable with no session at all, or authenticated by something other than a
# session (a setup code, an invite token, an internal token, a Slack OAuth state).
PUBLIC_ROUTES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/request-reset"),
    ("POST", "/api/auth/reset-password"),
    ("GET", "/api/auth/reset-password/validate"),
    ("POST", "/api/auth/verify-email"),
    ("GET", "/api/bootstrap/status"),
    ("POST", "/api/bootstrap/create-admin"),
    ("POST", "/api/bootstrap/generate-setup-code"),
    ("POST", "/api/bootstrap/verify-setup-code"),
    ("POST", "/api/users/accept-invite"),
    ("GET", "/api/health/live"),
    ("GET", "/api/health/ready"),
    ("GET", "/api/notifications/slack/callback"),
    ("GET", "/api/v1/settings/networking/self-check"),
    ("POST", "/api/v1/work-nodes/sessions/{session_id}/heartbeat"),
}

# Any signed-in caller, acting only on their own account or preferences.
SELF_SERVICE_ROUTES = {
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/refresh"),
    ("GET", "/api/auth/me"),
    ("PATCH", "/api/auth/me"),
    ("POST", "/api/auth/me/change-password"),
    ("GET", "/api/auth/me/session-credentials"),
    ("PUT", "/api/auth/me/session-credentials"),
    ("GET", "/api/auth/me/ssh-key"),
    ("POST", "/api/auth/me/ssh-key/generate"),
    ("GET", "/api/beta-features"),
    ("GET", "/api/dashboard/layout"),
    ("PUT", "/api/dashboard/layout"),
}

# Signed in, deliberately without a single gate. The search routes filter each
# result type by role and key scope; the rest are shared reference data.
DECLARED_ROUTES = {
    ("GET", "/api/search"),
    ("GET", "/api/search/quick"),
    ("GET", "/api/files/search"),
    ("GET", "/api/vocabularies"),
    ("GET", "/api/vocabularies/fields"),
    ("GET", "/api/components"),
}

# Not yet decided. This set only ever shrinks.
UNDECIDED_ROUTES = {
    ("POST", "/api/content-tokens"),
    ("GET", "/api/files/{file_id}/content"),
    ("GET", "/api/plots/{plot_id}/thumbnail/content"),
    ("GET", "/api/projects"),
    ("GET", "/api/snapshots"),
    ("GET", "/api/snapshots/compare"),
    ("GET", "/api/snapshots/{snapshot_id}"),
}

EXPERIMENT_READS = {
    ("GET", "/api/experiments"),
    ("GET", "/api/experiments/{experiment_id}"),
    ("GET", "/api/experiments/{experiment_id}/audit"),
    ("GET", "/api/experiments/{experiment_id}/files"),
    ("GET", "/api/experiments/{experiment_id}/sample-batches"),
    ("GET", "/api/experiments/{experiment_id}/samples"),
    ("GET", "/api/experiments/{experiment_id}/samples/csv-template"),
    ("GET", "/api/experiments/{experiment_id}/sequencing-batches"),
}

# The exact grants of routes whose gate is a decision worth pinning, because a
# wrong resource here would still look gated.
EXPECTED_GATES = {
    ("GET", "/api/documents"): {("files", "view")},
    ("GET", "/api/documents/{document_id}"): {("files", "view")},
    ("GET", "/api/documents/{document_id}/download"): {("files", "download")},
    ("GET", "/api/plots"): VIEW_RESULTS,
    ("GET", "/api/plots/{plot_id}"): VIEW_RESULTS,
    ("GET", "/api/plots/{plot_id}/thumbnail"): VIEW_RESULTS,
    ("GET", "/api/cellxgene"): VIEW_RESULTS,
    ("GET", "/api/cellxgene/{publication_id}"): VIEW_RESULTS,
    ("GET", "/api/cellxgene/publishable-files"): VIEW_RESULTS,
    ("GET", "/api/cellxgene/inspect/{file_id}"): VIEW_RESULTS,
    ("GET", "/api/storage/stats"): VIEW_INFRASTRUCTURE,
    ("GET", "/api/storage/lifecycle"): VIEW_INFRASTRUCTURE,
    ("GET", "/api/health/status"): VIEW_INFRASTRUCTURE,
    ("GET", "/api/health/services"): VIEW_INFRASTRUCTURE,
    **{route: VIEW_EXPERIMENTS for route in EXPERIMENT_READS},
    ("GET", "/api/samples/{sample_id}"): {("samples", "view")},
    ("GET", "/api/samples/{sample_id}/read-groups"): {("samples", "view")},
    ("GET", "/api/projects/{project_id}"): {("projects", "view")},
    ("GET", "/api/projects/{project_id}/provenance"): {("projects", "view")},
    ("GET", "/api/files"): {("files", "view")},
    ("GET", "/api/files/stats"): {("files", "view")},
    ("GET", "/api/files/{file_id}"): {("files", "view")},
    ("GET", "/api/datasets"): VIEW_EXPERIMENTS,
    ("GET", "/api/datasets/filter-options"): VIEW_EXPERIMENTS,
    ("GET", "/api/datasets/{experiment_id}"): VIEW_EXPERIMENTS,
    ("GET", "/api/templates"): VIEW_EXPERIMENTS,
    ("GET", "/api/templates/{template_id}"): VIEW_EXPERIMENTS,
    ("GET", "/api/sample-batches/{batch_id}"): VIEW_EXPERIMENTS,
    ("GET", "/api/sequencing-batches"): VIEW_EXPERIMENTS,
    ("GET", "/api/sequencing-batches/{batch_id}"): VIEW_EXPERIMENTS,
    ("GET", "/api/users"): {("users", "view")},
    ("POST", "/api/users"): {("users", "edit_role")},
    ("POST", "/api/users/bulk-invite"): {("users", "edit_role")},
    ("GET", "/api/users/{user_id}"): {("users", "view")},
    ("PATCH", "/api/users/{user_id}"): {("users", "edit_role")},
    ("POST", "/api/users/{user_id}/deactivate"): {("users", "deactivate")},
    ("POST", "/api/users/{user_id}/lock"): {("users", "deactivate")},
    ("POST", "/api/users/{user_id}/reactivate"): {("users", "deactivate")},
    ("DELETE", "/api/users/{user_id}"): {("users", "delete")},
    ("POST", "/api/users/{user_id}/resend-invite"): {("users", "invite")},
    ("POST", "/api/users/{user_id}/admin-reset-password"): {("users", "edit_role")},
    ("POST", "/api/components/select-batch"): CONFIGURE_INFRASTRUCTURE,
    ("POST", "/api/bootstrap/configure-org"): CONFIGURE_INFRASTRUCTURE,
    ("GET", "/api/bootstrap/smtp-settings"): CONFIGURE_INFRASTRUCTURE,
    ("POST", "/api/bootstrap/configure-smtp"): CONFIGURE_INFRASTRUCTURE,
    ("POST", "/api/bootstrap/test-smtp"): CONFIGURE_INFRASTRUCTURE,
    ("POST", "/api/bootstrap/complete"): CONFIGURE_INFRASTRUCTURE,
}


def _iter_routes(owner):
    """Concrete routes with full paths, through include_router wrappers and mounts."""
    for route in getattr(owner, "routes", []):
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from ((context.path, getattr(context, "original_route", context)) for context in contexts())
        elif hasattr(route, "dependant"):
            yield route.path, route
        elif hasattr(getattr(route, "app", None), "routes"):
            yield from ((route.path + path, inner) for path, inner in _iter_routes(route.app))


def _dependency_calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def _route_gates() -> dict[tuple[str, str], set[tuple[str, str]] | None]:
    """(method, path) -> the grants its gate accepts, or None when it has no gate."""
    gates: dict[tuple[str, str], set[tuple[str, str]] | None] = {}
    for path, route in _iter_routes(app):
        accepted: set[tuple[str, str]] | None = None
        for call in _dependency_calls(route.dependant):
            declared = getattr(call, "accepted_permissions", None)
            if declared is not None:
                accepted = (accepted or set()) | set(declared)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            gates[(method, path)] = accepted
    return gates


GATES = _route_gates()
UNGATED_BY_DECISION = PUBLIC_ROUTES | SELF_SERVICE_ROUTES | DECLARED_ROUTES | UNDECIDED_ROUTES


def test_the_route_table_was_actually_walked():
    assert len(GATES) > 500


def test_every_ungated_route_is_a_recorded_decision():
    ungated = {route for route, accepted in GATES.items() if accepted is None}
    assert ungated - UNGATED_BY_DECISION == set()


def test_no_decision_outlives_its_route_or_contradicts_a_gate():
    gated = {route for route, accepted in GATES.items() if accepted is not None}
    assert UNGATED_BY_DECISION - set(GATES) == set()
    assert UNGATED_BY_DECISION & gated == set()


def test_decision_sets_do_not_overlap():
    sets = [PUBLIC_ROUTES, SELF_SERVICE_ROUTES, DECLARED_ROUTES, UNDECIDED_ROUTES]
    assert sum(len(s) for s in sets) == len(UNGATED_BY_DECISION)


@pytest.mark.parametrize("route", sorted(EXPECTED_GATES), ids=lambda r: f"{r[0]} {r[1]}")
def test_route_accepts_exactly_its_documented_grants(route):
    assert GATES[route] == EXPECTED_GATES[route]


def test_every_exact_public_path_is_a_declared_public_route():
    """The middleware's allow-list and this contract must tell the same story."""
    declared_paths = {path for _, path in PUBLIC_ROUTES}
    documentation_paths = {p for p in PUBLIC_PATHS if p.endswith(("/docs", "/openapi.json", "/oauth2-redirect"))}
    assert PUBLIC_PATHS - documentation_paths - declared_paths == set()
