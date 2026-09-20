"""plan_8_5 section 3.5: the environment check, submitted through the path that already runs a
paper's own code.

What was missing was never the isolation. ``NotebookExecutionService.execute_fetched_code`` already
runs code fetched from a paper's authors under the untrusted identity, in its own namespace, with no
project-level role. What had no caller was the check that settles C1.B and C2.B: load the supplied
source in its declared runtime, and resolve the dependencies it declares.

So this is a caller, and the rules it is held to are the ones that identity exists for: it refuses
where the install has no isolated identity rather than borrowing another, it stages only what it was
given, and the result of a run is an outcome about the paper rather than an error.
"""

import pytest

from app.services.validation_environment_check import (
    EnvironmentCheckRefused,
    check_script,
    outcome_from_run,
    stage_environment_check,
)

_PY = {"path": "analysis.py", "language": "python", "text": "import numpy as np\nimport os\nprint(os.getcwd())\n"}
_R = {"path": "analysis.R", "language": "r", "text": "library(DESeq2)\nlibrary(dplyr)\nprint(1)\n"}


class TestTheScriptThatRuns:
    def test_the_python_check_imports_what_the_source_imports_and_nothing_else(self):
        script = check_script(sources=[_PY], manifests=[])
        assert "numpy" in script
        assert "importlib.import_module" in script
        assert "analysis.py" not in script, "it loads the modules, it does not run the paper's script"

    def test_the_r_check_attaches_what_the_source_attaches(self):
        script = check_script(sources=[_R], manifests=[])
        assert "DESeq2" in script and "dplyr" in script
        assert "requireNamespace" in script or "library" in script

    def test_the_script_reports_each_module_separately_so_a_failure_names_one(self):
        script = check_script(sources=[_PY], manifests=[])
        assert "BIOAF_LOAD" in script, "a marker the transcript can be read back from"

    def test_a_source_that_imports_only_its_own_runtime_still_has_a_check_to_run(self):
        script = check_script(sources=[{**_PY, "text": "import os\nprint(os.getcwd())\n"}], manifests=[])
        assert "os" in script


class TestWhatIsStaged:
    @pytest.mark.asyncio
    async def test_it_stages_the_sources_the_manifests_and_the_check(self, session, admin_user):
        written = {}

        class _Storage:
            def build_uri(self, bucket, path):
                return f"gs://{bucket}/{path}"

            async def write_text(self, uri, text, content_type=None):
                written[uri] = text

        staged = await stage_environment_check(
            sources=[_PY],
            manifests=[{"path": "requirements.txt", "text": "numpy==1.26.4\n"}],
            storage=_Storage(),
            bucket="untrusted-bucket",
            prefix="study-9",
        )
        assert staged["entry_point"].endswith(".py")
        assert any(uri.endswith("analysis.py") for uri in written)
        assert any(uri.endswith("requirements.txt") for uri in written)
        assert any(uri.endswith(staged["entry_point"]) for uri in written)
        assert staged["code_uri"].startswith("gs://untrusted-bucket/study-9/")

    @pytest.mark.asyncio
    async def test_nothing_is_staged_for_a_language_with_no_runtime(self, session, admin_user):
        class _Storage:
            def build_uri(self, bucket, path):
                return f"gs://{bucket}/{path}"

            async def write_text(self, uri, text, content_type=None):
                raise AssertionError("nothing should be staged")

        with pytest.raises(EnvironmentCheckRefused):
            await stage_environment_check(
                sources=[{**_PY, "language": "julia"}],
                manifests=[],
                storage=_Storage(),
                bucket="b",
                prefix="p",
            )


class TestWhatARunEstablishes:
    def test_a_clean_run_verifies_both_obligations(self):
        transcript = "BIOAF_LOAD numpy ok\nBIOAF_LOAD os ok\nBIOAF_RESOLVE ok\n"
        found = outcome_from_run(exit_code=0, transcript=transcript, environment="python:3.12-slim", ref="cs-1")
        assert found["load"]["status"] == "succeeded"
        assert found["dependency_resolution"]["status"] == "succeeded"
        assert found["load"]["environment"] == "python:3.12-slim"
        assert found["load"]["ref"] == "cs-1"

    def test_a_module_that_will_not_load_fails_the_load_and_names_it(self):
        transcript = "BIOAF_LOAD numpy ok\nBIOAF_LOAD scanpy failed: No module named 'scanpy'\n"
        found = outcome_from_run(exit_code=1, transcript=transcript, environment="python:3.12-slim", ref="cs-2")
        assert found["load"]["status"] == "failed"
        assert "scanpy" in found["load"]["reason"]

    def test_dependencies_that_will_not_resolve_fail_their_own_obligation(self):
        transcript = "BIOAF_RESOLVE failed: numpy==1.26.4 is not available for this runtime\n"
        found = outcome_from_run(exit_code=1, transcript=transcript, environment="python:3.12-slim", ref="cs-3")
        assert found["dependency_resolution"]["status"] == "failed"
        assert "numpy" in found["dependency_resolution"]["reason"]

    def test_a_run_that_said_nothing_establishes_nothing(self):
        """A pod that died before the check spoke is bioAF's limitation, not the paper's defect."""
        found = outcome_from_run(exit_code=137, transcript="", environment="python:3.12-slim", ref="cs-4")
        assert found == {}

    def test_a_timeout_establishes_nothing_either(self):
        found = outcome_from_run(exit_code=None, transcript="BIOAF_LOAD numpy ok\n", environment="e", ref="cs-5")
        assert found.get("load", {}).get("status") != "failed"
