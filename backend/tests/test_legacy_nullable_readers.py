"""Readers of columns every released schema allows to be NULL (plan 9, section 1).

The models now accept these NULLs (test_migrations_apply.LEGACY_NULLABLE). A NULL in one row must
not cost the caller the rows that are complete.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.notebook_sessions import _session_response


def _compute_session(accessed_files):
    return SimpleNamespace(
        id=1,
        session_type="jupyter",
        user=None,
        experiment=None,
        project=None,
        resource_profile="small",
        cpu_cores=2,
        memory_gb=8,
        requested_disk_gb=None,
        status="stopped",
        failure_reason=None,
        failure_message=None,
        idle_since=None,
        access_url=None,
        proxy_url=None,
        started_at=None,
        stopped_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        git_branch_name=None,
        git_commit_hash=None,
        environment_version_id=None,
        provider_metadata=None,
        accessed_files=accessed_files,
    )


def _link(file_id, access_type):
    return SimpleNamespace(file_id=file_id, access_type=access_type)


def test_recreate_keeps_the_complete_inputs_when_a_legacy_link_has_no_file():
    response = _session_response(
        _compute_session([_link(7, "input"), _link(None, "input"), _link(3, "input"), _link(9, "output")])
    )
    assert response.input_file_ids == [3, 7]


def test_a_link_with_no_recorded_direction_is_not_offered_as_an_input():
    response = _session_response(_compute_session([_link(7, "input"), _link(8, None)]))
    assert response.input_file_ids == [7]
