"""An initialized installation never re-creates its foundation buckets through Terraform.

Bootstrap applies the foundation module with local state in a temporary directory and never moves
that state into the remote bucket. After bootstrap, a foundation plan therefore runs against empty
state and proposes creating the existing state and backup buckets. Such a plan is refused and the
operator is pointed at the documented bucket-setting procedure instead.
"""

import json

import pytest
from sqlalchemy import text

from app.exceptions import StateError
from app.services.terraform_executor import TerraformExecutor
from tests.support.terraform_cli import terraform_cli
from tests.test_terraform_executor import (
    _make_plan_output,
    _mock_subprocess_run,
    _patch_work_dir,
    _seed_gcp_config,
    _seed_user,
)


def _show(*changes: tuple[str, list[str]]) -> str:
    return json.dumps(
        {
            "format_version": "1.2",
            "resource_changes": [
                {
                    "address": address,
                    "type": address.split(".")[0],
                    "name": address.split(".")[1],
                    "change": {"actions": actions, "before": None, "after": {}},
                }
                for address, actions in changes
            ],
        }
    )


async def _plan(session, user_id, show_stdout):
    def mock_run(cmd, **kwargs):
        if "show" in cmd:
            return _mock_subprocess_run(show_stdout)
        return _mock_subprocess_run(_make_plan_output(1))

    with terraform_cli(side_effect=mock_run), _patch_work_dir():
        return await TerraformExecutor.run_plan(session=session, user_id=user_id, module_name="foundation")


@pytest.mark.asyncio
@pytest.mark.parametrize("bucket", ["terraform_state", "backups"])
@pytest.mark.parametrize("actions", [["create"], ["delete", "create"]])
async def test_initialized_plan_that_recreates_a_foundation_bucket_is_refused(session, bucket, actions):
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    run = await _plan(session, user_id, _show((f"google_storage_bucket.{bucket}", actions)))

    assert run.status == "failed"
    assert "foundation" in run.error_message
    assert "docs/operations/foundation-bucket-guards.md" in run.error_message


@pytest.mark.asyncio
async def test_initialized_plan_that_only_updates_foundation_buckets_is_allowed(session):
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)

    run = await _plan(
        session,
        user_id,
        _show(("google_storage_bucket.terraform_state", ["update"]), ("google_storage_bucket.backups", ["update"])),
    )

    assert run.status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_uninitialized_foundation_create_is_still_allowed(session):
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=False)

    run = await _plan(session, user_id, _show(("google_storage_bucket.terraform_state", ["create"])))

    assert run.status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_a_stored_recreating_plan_cannot_be_applied_after_initialization(session):
    """A foundation plan recorded before initialization (or by an older release) is refused at apply."""
    user_id = await _seed_user(session)
    await _seed_gcp_config(session, configured=True, initialized=True)
    plan_json = {
        "total": 1,
        "resources": [{"address": "google_storage_bucket.backups", "action": "create"}],
    }
    result = await session.execute(
        text(
            "INSERT INTO terraform_runs (triggered_by_user_id, action, status, module_name, resources_planned, plan_json) "
            "VALUES (:uid, 'plan', 'awaiting_confirmation', 'foundation', 1, CAST(:plan AS JSONB)) RETURNING id"
        ).bindparams(uid=user_id, plan=json.dumps(plan_json))
    )
    run_id = result.scalar_one()
    await session.commit()

    with pytest.raises(StateError, match="foundation-bucket-guards"):
        async for _ in TerraformExecutor.run_apply(session=session, run_id=run_id, user_id=user_id):
            pass


def test_aws_state_bucket_recreation_is_detected():
    plan = {"resources": [{"address": "aws_s3_bucket.this", "action": "create"}]}
    assert TerraformExecutor.foundation_recreations(plan) == ["aws_s3_bucket.this"]
    assert TerraformExecutor.foundation_recreations({"resources": []}) == []
