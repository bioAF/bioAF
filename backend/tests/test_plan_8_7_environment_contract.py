"""plan_8_7 stage 3: the environment check executes its recorded contract and reports what it saw.

The owner's September 21 assessment:

    "Package imports establish stronger environment claims than they measure."

Four separate defects behind that sentence, and the plan names each one:

1. the check imported the paper's modules against whatever image this install configures. It never
   installed the paper's declared dependencies, so a repository whose environment cannot be built at
   all looked identical to one whose packages happen to be in the base image;
2. `LIMITS` was recorded on the request and never reached the executor, so the report stated a cpu,
   memory, timeout and network policy that nothing applied;
3. loading a package root was read as verifying the interface the source uses from it, and a declared
   version was never compared with the installed one;
4. the cause was attributed by COUNTING missing packages: more than one, and all of them, meant
   bioAF's environment. A paper with two genuinely broken dependencies got the same answer as an
   unprovisioned image.
"""

import pytest

from app.services.validation_environment_check import (
    INSTALL_MARKER,
    INTERFACE_MARKER,
    LOAD_MARKER,
    RESOLVE_MARKER,
    VERSION_MARKER,
    EnvironmentCheckRefused,
    check_script,
    environment_check_request,
    outcome_from_run,
)

_SOURCE = {
    "path": "analysis.py",
    "language": "python",
    "text": "import numpy as np\nimport scanpy\n\nscanpy.pp.pca(None)\nprint(np.mean([1]))\n",
}
_MANIFEST = {"path": "requirements.txt", "text": "numpy==1.26.4\nscanpy==1.9.3\n"}


class TestTheSpecificationIsWhatWillActuallyRun:
    def test_it_names_the_runtime_the_files_and_their_hashes(self):
        request = environment_check_request(sources=[_SOURCE], manifests=[_MANIFEST])
        assert request["runtime"]
        assert request["source_digest"]
        assert request["manifest_digest"]

    def test_it_names_the_resource_profile_the_timeout_and_the_network_policy(self):
        request = environment_check_request(sources=[_SOURCE], manifests=[_MANIFEST])
        assert request["resource_profile"]
        assert request["limits"]["timeout_seconds"] > 0
        assert request["limits"]["network"] in ("denied", "install_only")

    def test_installing_dependencies_declares_its_own_network_access(self):
        """plan_8_7 stage 3: "Any permitted installation network access must be explicit in the
        authorized specification.\""""
        request = environment_check_request(sources=[_SOURCE], manifests=[_MANIFEST])
        assert request["phases"]["install"]["network"] == "allowed"
        assert request["phases"]["runtime"]["network"] == "denied"
        assert request["limits"]["network"] == "install_only"

    def test_with_no_manifest_nothing_installs_and_runtime_network_stays_denied(self):
        request = environment_check_request(sources=[_SOURCE], manifests=[])
        assert request["phases"]["install"]["network"] == "denied"
        assert request["phases"]["runtime"]["network"] == "denied"

    def test_a_working_directory_is_part_of_the_contract(self):
        assert environment_check_request(sources=[_SOURCE], manifests=[_MANIFEST])["working_directory"]

    def test_an_unsupported_runtime_refuses_rather_than_recording_a_setting(self):
        julia = {"path": "a.jl", "language": "julia", "text": "1"}
        with pytest.raises(EnvironmentCheckRefused):
            environment_check_request(sources=[julia], manifests=[])


class TestTheScriptRunsTheFourPhases:
    def test_it_installs_the_declared_dependencies_first(self):
        script = check_script(sources=[_SOURCE], manifests=[_MANIFEST])
        assert INSTALL_MARKER in script
        assert "requirements.txt" in script
        assert script.index(INSTALL_MARKER) < script.index(LOAD_MARKER)

    def test_it_compares_the_declared_version_with_the_installed_one(self):
        script = check_script(sources=[_SOURCE], manifests=[_MANIFEST])
        assert VERSION_MARKER in script
        assert "1.26.4" in script

    def test_it_checks_the_symbols_the_source_actually_uses(self):
        script = check_script(sources=[_SOURCE], manifests=[_MANIFEST])
        assert INTERFACE_MARKER in script
        assert "scanpy.pp" in script or "pp.pca" in script

    def test_with_no_manifest_it_still_loads_and_says_nothing_was_installed(self):
        script = check_script(sources=[_SOURCE], manifests=[])
        assert f"{INSTALL_MARKER} none" in script
        assert LOAD_MARKER in script


def _transcript(*lines):
    return "\n".join(lines)


class TestInstallationFailuresSurviveASuccessfulImport:
    def test_a_failed_install_is_recorded_even_when_the_base_image_holds_the_package(self):
        found = outcome_from_run(
            exit_code=0,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt failed: ERROR: No matching distribution found for scanpy==1.9.3",
                f"{LOAD_MARKER} numpy ok",
                f"{LOAD_MARKER} scanpy ok",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["installation"]["status"] == "failed"
        assert "scanpy==1.9.3" in found["installation"]["reason"]

    def test_the_import_against_the_base_image_does_not_establish_the_environment(self):
        found = outcome_from_run(
            exit_code=0,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt failed: ERROR: No matching distribution found for scanpy==1.9.3",
                f"{LOAD_MARKER} scanpy ok",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["load"]["status"] != "succeeded"
        assert found["dependency_resolution"]["status"] == "failed"

    def test_a_clean_install_and_clean_imports_establish_both(self):
        found = outcome_from_run(
            exit_code=0,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt ok",
                f"{LOAD_MARKER} numpy ok",
                f"{LOAD_MARKER} scanpy ok",
                f"{VERSION_MARKER} numpy declared=1.26.4 observed=1.26.4 ok",
                f"{INTERFACE_MARKER} scanpy.pp ok",
                f"{RESOLVE_MARKER} ok",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["installation"]["status"] == "succeeded"
        assert found["load"]["status"] == "succeeded"
        assert found["dependency_resolution"]["status"] == "succeeded"


class TestVersionsAndInterfacesAreTheirOwnObservations:
    def test_a_version_mismatch_is_recorded_and_does_not_read_as_a_clean_environment(self):
        found = outcome_from_run(
            exit_code=0,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt ok",
                f"{LOAD_MARKER} numpy ok",
                f"{VERSION_MARKER} numpy declared=1.26.4 observed=2.1.0 mismatch",
                f"{RESOLVE_MARKER} ok",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["versions"]["status"] == "mismatch"
        assert "numpy" in found["versions"]["reason"]
        assert found["dependency_resolution"]["status"] != "succeeded"

    def test_a_missing_symbol_is_a_failure_of_the_interface_not_of_the_import(self):
        found = outcome_from_run(
            exit_code=1,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt ok",
                f"{LOAD_MARKER} scanpy ok",
                f"{INTERFACE_MARKER} scanpy.pp missing",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["load"]["status"] == "succeeded"
        assert found["interfaces"]["status"] == "missing"
        assert "scanpy.pp" in found["interfaces"]["reason"]

    def test_an_interface_nobody_could_check_is_left_open_rather_than_verified(self):
        found = outcome_from_run(
            exit_code=0,
            transcript=_transcript(
                f"{INSTALL_MARKER} none",
                f"{LOAD_MARKER} numpy ok",
                f"{INTERFACE_MARKER} numpy.mean unchecked",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["interfaces"]["status"] == "unchecked"


class TestTheCauseComesFromWhatWasObserved:
    def test_two_broken_dependencies_in_a_built_environment_are_a_real_finding(self):
        """The old rule read "more than one missing, and all of them" as bioAF's environment. Two
        genuinely broken dependencies in an environment that BUILT are a fact about the paper."""
        found = outcome_from_run(
            exit_code=1,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt ok",
                f"{LOAD_MARKER} scanpy failed: No module named 'scanpy'",
                f"{LOAD_MARKER} numpy failed: No module named 'numpy'",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["load"]["status"] == "failed"
        assert "scanpy" in found["load"]["reason"]

    def test_the_same_failures_with_nothing_installed_establish_nothing(self):
        """No installation was performed, so the image is whatever this install configures and every
        package is "missing" for a reason that is bioAF's, not the paper's."""
        found = outcome_from_run(
            exit_code=1,
            transcript=_transcript(
                f"{INSTALL_MARKER} none",
                f"{LOAD_MARKER} scanpy failed: No module named 'scanpy'",
                f"{LOAD_MARKER} numpy failed: No module named 'numpy'",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found == {} or found.get("load", {}).get("status") not in ("failed", "succeeded")

    def test_an_install_that_could_not_reach_the_network_is_bioafs_limitation(self):
        found = outcome_from_run(
            exit_code=1,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt failed: Could not fetch URL https://pypi.org/simple: connection refused",
                f"{LOAD_MARKER} scanpy failed: No module named 'scanpy'",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["installation"]["status"] == "failed"
        assert found["installation"]["attributed_to"] == "bioaf"
        assert "dependency_resolution" not in found or found["dependency_resolution"]["status"] != "failed"

    def test_a_dependency_that_does_not_resolve_is_the_papers(self):
        found = outcome_from_run(
            exit_code=1,
            transcript=_transcript(
                f"{INSTALL_MARKER} requirements.txt failed: ERROR: No matching distribution found for scanpy==1.9.3",
            ),
            environment="python:3.12-slim",
            ref="compute-session-1",
        )
        assert found["installation"]["attributed_to"] == "paper"
        assert found["dependency_resolution"]["status"] == "failed"

    def test_a_run_that_said_nothing_at_all_still_establishes_nothing(self):
        assert outcome_from_run(exit_code=1, transcript="", environment="x", ref="y") == {}
