"""Plan 03.5C: a session that cannot work fails plainly instead of appearing to start.

An environment whose build proved it has no RStudio (or no Jupyter) is refused at launch for that
tool. An image without the tool's executable, discovered only when the container starts, fails with a
plain reason instead of a generic stop. Unverified (older) images are not refused.
"""

import pytest
from sqlalchemy import text

from app.adapters.notebooks.lifecycle import assess_pod
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from tests.support.notebook_k8s_fakes import notebook_pod


async def _env_version(session, user, capability):
    env = Environment(
        name="Py only", organization_id=user.organization_id, created_by_user_id=user.id, environment_type="notebook"
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: py\ndependencies: [python]\n",
        image_uri="reg/py:v1.1",
        created_by_user_id=user.id,
        notebook_capability=capability,
    )
    session.add(version)
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('compute_deployed', 'true') ON CONFLICT (key) DO UPDATE SET value = 'true'"
        )
    )
    await session.commit()
    return version


async def _launch(client, token, version_id, session_type):
    return await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": session_type, "resource_profile": "small", "environment_version_id": version_id},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability, session_type, message",
    [
        ({"contract": 1, "jupyter": "verified", "rstudio": "unsupported"}, "rstudio", "does not include RStudio"),
        ({"contract": 1, "jupyter": "failed", "rstudio": "verified"}, "jupyter", "does not include Jupyter"),
    ],
)
async def test_a_tool_the_environment_was_proven_not_to_have_is_refused_before_launch(
    client, session, admin_user, admin_token, capability, session_type, message
):
    version = await _env_version(session, admin_user, capability)
    response = await _launch(client, admin_token, version.id, session_type)
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert (await session.execute(text("SELECT count(*) FROM compute_sessions"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_an_unverified_environment_is_not_refused(client, session, admin_user, admin_token):
    version = await _env_version(session, admin_user, None)
    response = await _launch(client, admin_token, version.id, "rstudio")
    assert "does not include" not in response.text


def _terminated(exit_code, reason="Error"):
    from kubernetes.client import V1ContainerState, V1ContainerStateTerminated, V1ContainerStatus

    pod = notebook_pod(phase="Failed", ready=False, notebook=None)
    pod.status.container_statuses = [
        V1ContainerStatus(
            name="notebook",
            state=V1ContainerState(terminated=V1ContainerStateTerminated(exit_code=exit_code, reason=reason)),
            ready=False,
            restart_count=0,
            image="i",
            image_id="i",
        )
    ]
    return pod


@pytest.mark.parametrize(
    "exit_code, reason", [(127, "Error"), (126, "Error"), (128, "StartError"), (128, "ContainerCannotRun")]
)
def test_an_image_without_the_tool_fails_with_a_plain_reason(exit_code, reason):
    assessment = assess_pod(_terminated(exit_code, reason), [])
    assert assessment.state == "failed"
    assert assessment.reason == "incompatible_image"
    assert assessment.message == "This environment cannot start the selected tool. Choose an environment built for it."


def test_other_exits_keep_the_existing_reason():
    assessment = assess_pod(_terminated(1), [])
    assert assessment.state == "failed" and assessment.reason != "incompatible_image"
