"""Output persistence, configuration clearing, and bucket emptying.

Those three contracts overlap in their keys and differ in purpose, so each is
pinned on its own.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services.deployment_outputs import (
    COMPUTE_AWS_OUTPUTS,
    COMPUTE_OUTPUTS,
    COMPUTE_TEARDOWN_CLEARED_KEYS,
    IMAGE_BUILD_OUTPUTS,
    STORAGE_BUCKETS_TO_EMPTY,
    STORAGE_DEPLOY_OUTPUTS,
    STORAGE_DESTROY_CLEARED_KEYS,
    STORAGE_SYNC_OUTPUTS,
    BlankOutputs,
    persist_outputs,
)
from tests.test_deployment_characterization import _committed, _event, _outputs, _seed_config, _seed_user

ALL_BUCKETS = {
    "ingest_bucket_name",
    "raw_bucket_name",
    "working_bucket_name",
    "results_bucket_name",
    "references_bucket_name",
    "literature_bucket_name",
    "config_backups_bucket_name",
}
PUBSUB = {"pubsub_topic_name", "pubsub_subscription_name"}


@pytest.mark.asyncio
async def test_skip_policy_leaves_blank_keys_alone(session, db_engine):
    await _seed_config(session, gke_cluster_name="existing")

    populated = await persist_outputs(
        session,
        _outputs(cluster_endpoint="https://x", cluster_name=""),
        {"cluster_name": "gke_cluster_name", "cluster_endpoint": "gke_cluster_endpoint"},
        BlankOutputs.SKIP,
    )
    await session.commit()

    assert populated == {"gke_cluster_endpoint": "https://x"}
    assert await _committed(db_engine, "gke_cluster_name") == "existing"


@pytest.mark.asyncio
async def test_write_null_policy_records_blanks(session, db_engine):
    populated = await persist_outputs(
        session,
        _outputs(cluster_endpoint="https://x"),
        {"cluster_name": "gke_cluster_name", "cluster_endpoint": "gke_cluster_endpoint"},
        BlankOutputs.WRITE_NULL,
    )
    await session.commit()

    assert populated == {"gke_cluster_endpoint": "https://x"}
    assert await _committed(db_engine, "gke_cluster_name") == "null"


def test_storage_sync_is_the_deploy_mapping_without_pubsub():
    assert set(STORAGE_DEPLOY_OUTPUTS.values()) == ALL_BUCKETS | PUBSUB
    assert set(STORAGE_SYNC_OUTPUTS.values()) == ALL_BUCKETS


def test_bucket_emptying_receives_only_bucket_names():
    assert set(STORAGE_BUCKETS_TO_EMPTY) == ALL_BUCKETS


def test_storage_destroy_clears_every_supported_name():
    assert set(STORAGE_DESTROY_CLEARED_KEYS) == {"storage_deployed"} | ALL_BUCKETS | PUBSUB


def test_compute_teardown_clearing_contract_is_only_cluster_identity():
    assert set(COMPUTE_TEARDOWN_CLEARED_KEYS) == {"gke_cluster_name", "gke_cluster_endpoint", "gke_cluster_ca_cert"}


def test_provider_specific_keys_stay_provider_specific():
    assert not set(COMPUTE_AWS_OUTPUTS.values()) & set(COMPUTE_OUTPUTS.values())
    assert COMPUTE_AWS_OUTPUTS["notebook_runner_sa_email"] == "notebook_runner_role_arn"
    assert IMAGE_BUILD_OUTPUTS == {"codebuild_project_name": "aws_codebuild_project"}


@pytest.mark.asyncio
async def test_aws_sync_restores_supported_outputs(session, db_engine):
    from app.services.stack_deployment import sync_compute_config

    await _seed_config(session, cloud_provider="aws", aws_work_node_subnet_id="keep-me")
    outputs = _outputs(
        cluster_name="eks",
        notebook_runner_sa_email="arn:notebook",
        cellxgene_runner_sa_email="arn:cellxgene",
        pipeline_runner_role_arn="arn:pipeline",
        oidc_provider_arn="arn:oidc",
        oidc_provider_url="https://oidc",
        cluster_autoscaler_role_arn="arn:ca",
        work_node_subnet_id="",
        work_node_security_group_id="sg-1",
        work_node_instance_profile="profile",
    )

    with patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)):
        populated = await sync_compute_config(session)
    await session.commit()

    assert populated["pipeline_runner_role_arn"] == "arn:pipeline"
    assert await _committed(db_engine, "notebook_runner_role_arn") == "arn:notebook"
    assert await _committed(db_engine, "cellxgene_runner_role_arn") == "arn:cellxgene"
    assert await _committed(db_engine, "eks_oidc_provider_arn") == "arn:oidc"
    assert await _committed(db_engine, "eks_oidc_provider_url") == "https://oidc"
    assert await _committed(db_engine, "cluster_autoscaler_role_arn") == "arn:ca"
    assert await _committed(db_engine, "aws_work_node_security_group_id") == "sg-1"
    assert await _committed(db_engine, "aws_work_node_instance_profile") == "profile"
    # Sync skips blanks, unlike the deploy hook.
    assert await _committed(db_engine, "aws_work_node_subnet_id") == "keep-me"
    assert await _committed(db_engine, "notebook_runner_sa_email") == "arn:notebook"


@pytest.mark.asyncio
async def test_gcp_sync_writes_no_aws_keys(session, db_engine):
    from app.services.stack_deployment import sync_compute_config

    await _seed_config(session, cloud_provider="gcp")
    outputs = _outputs(cluster_name="gke", pipeline_runner_role_arn="stray")

    with patch("app.services.stack_deployment.TerraformExecutor.read_module_outputs", AsyncMock(return_value=outputs)):
        populated = await sync_compute_config(session)
    await session.commit()

    assert populated == {"gke_cluster_name": "gke"}
    assert await _committed(db_engine, "pipeline_runner_role_arn") is None


@pytest.mark.asyncio
async def test_aws_compute_deploy_persists_provider_outputs(session, db_engine):
    from tests.test_deployment_characterization import _deploy

    user_id = await _seed_user(session)
    await _seed_config(
        session,
        cloud_provider="aws",
        aws_account_id="111122223333",
        aws_region="us-west-1",
        terraform_initialized="true",
        compute_deployed="false",
        storage_deployed="true",
        aws_codebuild_project="bioaf-build",
    )

    with patch("app.services.stack_deployment._ensure_aws_cluster_autoscaler", AsyncMock(return_value="installed")):
        await _deploy(
            session,
            user_id,
            {
                "compute": [
                    _event(
                        "apply_complete",
                        extra={"outputs": _outputs(cluster_name="eks", notebook_runner_sa_email="arn:notebook")},
                    )
                ]
            },
        )
    await session.commit()

    assert await _committed(db_engine, "notebook_runner_role_arn") == "arn:notebook"
    assert await _committed(db_engine, "notebook_runner_sa_email") == "arn:notebook"
    assert await _committed(db_engine, "pipeline_runner_role_arn") == "null"
    assert await _committed(db_engine, "aws_work_node_subnet_id") == "null"


@pytest.mark.asyncio
async def test_storage_destroy_clears_reference_and_literature_buckets(session, db_engine):
    from app.services.stack_deployment import destroy_storage

    user_id = await _seed_user(session)
    await _seed_config(
        session,
        gcp_credentials_configured="true",
        compute_deployed="false",
        storage_deployed="true",
        references_bucket_name="bioaf-refs",
        literature_bucket_name="bioaf-lit",
        raw_bucket_name="bioaf-raw",
        pubsub_topic_name="topic",
    )

    async def destroy(_s, _u, _m):
        yield _event("apply_complete")

    with (
        patch("app.services.stack_deployment._empty_gcs_bucket", AsyncMock(return_value=0)),
        patch("app.services.stack_deployment._run_destroy", side_effect=destroy),
    ):
        async for _ in destroy_storage(session, user_id=user_id):
            pass
    await session.commit()

    for key in ("references_bucket_name", "literature_bucket_name", "raw_bucket_name", "pubsub_topic_name"):
        assert await _committed(db_engine, key) == "null", key
    rows = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'storage_deployed'"))).one()
    assert rows[0] == "null"
