"""Plan 03.5B: a notebook build's capability comes from the build's own evidence, bound to a digest.

Each image build reads the runtime check's result out of the image it just built and returns it,
with the pushed image's digest, through the build provider (Cloud Build step output, CodeBuild
exported variables). "Verified" is recorded only from that evidence and only with a digest, and
launches use the digest, so a moved tag never changes what a verified version runs. A build without
evidence (for example one that was already running when bioAF was upgraded) stays unverified.
"""

import base64
import json
from unittest.mock import patch

import pytest

from app.adapters.image_build.base import BuildResult
from app.adapters.image_build.gcp import GcpCloudBuildProvider
from app.services import notebook_runtime

CONFIG = {"project_id": "my-project", "region": "us-central1"}
IMAGE = "us-central1-docker.pkg.dev/my-project/bioaf-images/env-7:v1.2"
DIGEST = "sha256:" + "a" * 64
EVIDENCE = {"contract": 1, "jupyter": "verified", "rstudio": "unsupported"}


# -- Cloud Build --------------------------------------------------------------------------------


def _gcp_submit():
    with patch("app.adapters.image_build.gcp.authorized_request") as req:
        req.return_value = {"metadata": {"build": {"id": "b-1"}}}
        GcpCloudBuildProvider().submit_build(
            object(), CONFIG, context_object_uri="gs://b/o", image_uri=IMAGE, build_sa=None, timeout="3600s"
        )
    return req.call_args.args[3]


def test_cloud_build_reads_the_check_result_out_of_the_image_it_built():
    steps = _gcp_submit()["steps"]
    assert steps[0]["args"][:3] == ["build", "-t", IMAGE]
    evidence = steps[1]
    script = " ".join(evidence["args"])
    assert notebook_runtime.CAPABILITY_FILE in script and IMAGE in script
    # $$ escapes Cloud Build substitution, so the step writes to the real $BUILDER_OUTPUT.
    assert "$$BUILDER_OUTPUT/output" in script


def test_cloud_build_result_carries_status_digest_and_evidence():
    output = base64.b64encode(json.dumps(EVIDENCE).encode()).decode()
    reply = {
        "status": "SUCCESS",
        "results": {
            "images": [{"name": IMAGE, "digest": DIGEST}],
            "buildStepOutputs": ["", output],
        },
    }
    with patch("app.adapters.image_build.gcp.authorized_request", return_value=reply):
        result = GcpCloudBuildProvider().build_result(object(), CONFIG, "b-1")
    assert result == BuildResult(status="SUCCESS", image_digest=DIGEST, evidence=EVIDENCE)


@pytest.mark.parametrize(
    "results",
    [{}, {"images": [{"name": IMAGE, "digest": DIGEST}]}, {"buildStepOutputs": ["", "bm90LWpzb24="]}],
)
def test_a_cloud_build_without_readable_evidence_returns_none_for_it(results):
    with patch(
        "app.adapters.image_build.gcp.authorized_request", return_value={"status": "SUCCESS", "results": results}
    ):
        result = GcpCloudBuildProvider().build_result(object(), CONFIG, "b-1")
    assert result.status == "SUCCESS"
    assert result.evidence is None


def test_an_unreadable_cloud_build_is_unknown():
    with patch("app.adapters.image_build.gcp.authorized_request", side_effect=RuntimeError("boom")):
        assert GcpCloudBuildProvider().build_result(object(), CONFIG, "b-1") == BuildResult("UNKNOWN", None, None)


# -- CodeBuild ----------------------------------------------------------------------------------

AWS_CONFIG = {"account_id": "043671579834", "region": "us-west-1", "codebuild_project": "bioaf-image-build"}


def test_codebuild_exports_the_digest_and_the_check_result():
    from app.adapters.image_build.aws import _BUILDSPEC

    import yaml

    spec = yaml.safe_load(_BUILDSPEC)
    assert spec["env"]["exported-variables"] == ["IMAGE_DIGEST", "NOTEBOOK_CAPABILITY"]
    post = " ".join(spec["phases"]["post_build"]["commands"])
    assert post.index("docker push") < post.index("IMAGE_DIGEST=")
    assert notebook_runtime.CAPABILITY_FILE in post


def test_codebuild_result_carries_status_digest_and_evidence():
    from app.adapters.image_build.aws import CodeBuildImageBuildProvider

    exported = [
        {"name": "IMAGE_DIGEST", "value": DIGEST},
        {"name": "NOTEBOOK_CAPABILITY", "value": base64.b64encode(json.dumps(EVIDENCE).encode()).decode()},
    ]
    with patch.object(CodeBuildImageBuildProvider, "_client") as mk:
        mk.return_value.batch_get_builds.return_value = {
            "builds": [{"buildStatus": "SUCCEEDED", "exportedEnvironmentVariables": exported}]
        }
        result = CodeBuildImageBuildProvider().build_result(None, AWS_CONFIG, "id")
    assert result == BuildResult(status="SUCCESS", image_digest=DIGEST, evidence=EVIDENCE)


def test_a_codebuild_without_exports_has_no_evidence():
    from app.adapters.image_build.aws import CodeBuildImageBuildProvider

    with patch.object(CodeBuildImageBuildProvider, "_client") as mk:
        mk.return_value.batch_get_builds.return_value = {"builds": [{"buildStatus": "SUCCEEDED"}]}
        result = CodeBuildImageBuildProvider().build_result(None, AWS_CONFIG, "id")
    assert result == BuildResult(status="SUCCESS", image_digest=None, evidence=None)


# -- What is recorded ---------------------------------------------------------------------------

CONDA = "name: e\ndependencies: [python=3.11, jupyterlab]\n"


def test_evidence_with_a_digest_is_recorded_as_verified():
    capability = notebook_runtime.capability_from_build(
        "conda", CONDA, BuildResult("SUCCESS", DIGEST, EVIDENCE), image_uri=IMAGE, build_id="b-1"
    )
    assert capability == {
        "contract": 1,
        "jupyter": "verified",
        "rstudio": "unsupported",
        "image_uri": IMAGE,
        "image_digest": DIGEST,
        "build_id": "b-1",
    }


@pytest.mark.parametrize(
    "result",
    [
        BuildResult("SUCCESS", None, EVIDENCE),  # no digest to bind the evidence to
        BuildResult("SUCCESS", DIGEST, None),  # a build that predates the evidence step
        BuildResult("SUCCESS", DIGEST, {**EVIDENCE, "contract": 0}),  # another contract
        BuildResult("SUCCESS", "latest", EVIDENCE),  # not a digest
        BuildResult("SUCCESS", DIGEST, {"contract": 1, "jupyter": "yes", "rstudio": "maybe"}),
    ],
)
def test_anything_less_is_unverified(result):
    capability = notebook_runtime.capability_from_build("conda", CONDA, result, image_uri=IMAGE, build_id="b-1")
    assert (capability["jupyter"], capability["rstudio"]) == ("unverified", "unverified")


def test_a_users_own_dockerfile_cannot_claim_verification_by_writing_the_file():
    definition = "FROM python:3.12\nRUN mkdir -p /etc/bioaf && echo '{}' > /etc/bioaf/notebook-capability.json\n"
    capability = notebook_runtime.capability_from_build(
        "dockerfile", definition, BuildResult("SUCCESS", DIGEST, EVIDENCE), image_uri=IMAGE, build_id="b-1"
    )
    assert (capability["jupyter"], capability["rstudio"]) == ("unverified", "unverified")


def test_a_verified_version_launches_by_digest_and_an_unverified_one_by_its_uri():
    verified = {"image_digest": DIGEST, "jupyter": "verified"}
    assert notebook_runtime.launch_image(IMAGE, verified) == (
        "us-central1-docker.pkg.dev/my-project/bioaf-images/env-7@" + DIGEST
    )
    assert notebook_runtime.launch_image("localhost:5000/env:v1", verified) == "localhost:5000/env@" + DIGEST
    assert notebook_runtime.launch_image(IMAGE, {"jupyter": "unverified"}) == IMAGE
    assert notebook_runtime.launch_image(IMAGE, None) == IMAGE
