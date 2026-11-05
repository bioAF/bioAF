"""The custom launcher's decisions, tested directly from explicit inputs.

Whether a version may be launched, and which variable values a run gets, are decided without a
database or compute: ``check_launchable`` takes the loaded version, and ``resolve_variables`` the
version's definitions and what the caller provided.
"""

from types import SimpleNamespace

import pytest

from app.exceptions import StateError, ValidationError
from app.services.custom_pipeline_service import CustomPipelineService


def _version(
    *, org_id=1, pipeline_id=10, status="active", env_status="ready", env=True, cpu="1", memory="4Gi"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=99,
        version_number=3,
        status=status,
        cpu_request=cpu,
        memory_request=memory,
        custom_pipeline=SimpleNamespace(id=pipeline_id, organization_id=org_id),
        environment_version=SimpleNamespace(status=env_status, image_uri="registry/p:1") if env else None,
    )


def test_an_active_version_with_a_ready_environment_is_launchable_and_yields_its_image():
    assert CustomPipelineService.check_launchable(_version(), org_id=1, pipeline_id=10) == "registry/p:1"


@pytest.mark.parametrize(
    "version,pipeline_id,error",
    [
        (_version(org_id=2), None, ValidationError),
        (_version(pipeline_id=11), 10, ValidationError),
        (_version(status="deprecated"), None, StateError),
        (_version(env=False), None, ValidationError),
        (_version(env_status="building"), None, StateError),
    ],
    ids=["other-organization", "other-pipeline", "deprecated", "no-environment", "environment-not-ready"],
)
def test_what_makes_a_version_unlaunchable(version, pipeline_id, error):
    with pytest.raises(error):
        CustomPipelineService.check_launchable(version, org_id=1, pipeline_id=pipeline_id)


def _var(name, *, type_="string", default=None, required=False):
    return SimpleNamespace(variable_name=name, variable_type=type_, default_value=default, is_required=required)


def _given(**values):
    return [SimpleNamespace(variable_name=k, variable_value=v) for k, v in values.items()]


def test_provided_values_win_defaults_fill_in_and_unset_optionals_stay_unset():
    defined = [_var("depth", type_="number", default="10"), _var("label"), _var("mode", default="fast")]
    assert CustomPipelineService.resolve_variables(defined, _given(depth="25")) == {"depth": "25", "mode": "fast"}


def test_a_missing_required_variable_is_refused():
    with pytest.raises(ValidationError, match="Required variable missing: sample"):
        CustomPipelineService.resolve_variables([_var("sample", required=True)], [])


def test_an_unknown_variable_is_refused():
    with pytest.raises(ValidationError, match="Unknown variable"):
        CustomPipelineService.resolve_variables([], _given(extra="1"))


@pytest.mark.parametrize("type_,value", [("number", "ten"), ("boolean", "yes")])
def test_a_value_of_the_wrong_type_is_refused(type_, value):
    with pytest.raises(ValidationError):
        CustomPipelineService.resolve_variables([_var("v", type_=type_)], _given(v=value))


# --- What a refused launch says: plain words on screen, the technical state in the logs ---


@pytest.mark.parametrize(
    "version,plain",
    [
        (_version(status="deprecated"), "deprecated"),
        (_version(env=False), "no environment"),
        (_version(env_status="draft"), "has not been built"),
        (_version(env_status="building"), "still building"),
        (_version(env_status="failed"), "did not build"),
        (_version(cpu="2", memory="8Gi"), "at most 1.4 CPU and 5Gi"),
    ],
    ids=["deprecated", "no-environment", "environment-draft", "environment-building", "environment-failed", "oversize"],
)
def test_a_refused_launch_says_why_in_plain_words(version, plain):
    with pytest.raises((StateError, ValidationError)) as refused:
        CustomPipelineService.check_launchable(version, org_id=1, pipeline_id=None)
    message = str(refused.value)
    assert plain in message
    for technical in ("expected", "status '", "has status"):
        assert technical not in message


# --- A launch value given twice ---


def test_the_same_value_given_twice_is_accepted():
    assert CustomPipelineService.resolve_variables(
        [_var("greeting")], _given_list(("greeting", "hi"), ("greeting", "hi"))
    ) == {"greeting": "hi"}


def test_two_different_values_for_one_variable_are_refused():
    with pytest.raises(ValidationError, match="greeting"):
        CustomPipelineService.resolve_variables(
            [_var("greeting")], _given_list(("greeting", "hi"), ("greeting", "bye"))
        )


def _given_list(*pairs):
    return [SimpleNamespace(variable_name=k, variable_value=v) for k, v in pairs]
