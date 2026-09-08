"""plan_7 step 17: what the pod actually does with the authors' code.

`execute_fetched_code` builds a spec carrying `fetched_code`; the headless branch of the pod
manifest required `notebook_json` and raised without it, so every fetched-code launch would have
failed at the boundary. This is the branch that runs it instead.

**Failure is the RESULT, not an error to refuse on.** The whole script runs under a transcript, and
the transcript is written and synced whatever happens. A repo whose dependencies will not install is
a real finding about that paper, and it is only a finding if the log survives to be read.

Dependencies install the ordinary way, over the network, because that is what a notebook session
does. The two-phase, network-off design an earlier draft called for was answering a security
question this is not: a notebook session is already an isolated per-session pod on a private node,
and step 16a gave this one an identity that can reach exactly one bucket.
"""

import pytest

from app.adapters.notebooks.kubernetes import build_fetched_code_script


def _script(**kw) -> str:
    return build_fetched_code_script(
        {
            "code_uri": kw.pop("code_uri", "gs://bioaf-untrusted-x/study-1/code.tar.gz"),
            "entry_point": kw.pop("entry_point", "scripts/run_deseq2.R"),
            "arguments": kw.pop("arguments", ""),
        },
        copy_in=kw.pop("copy_in", "gcloud storage cp {uri} {local}"),
        auth=kw.pop("auth", ""),
        timeout_seconds=kw.pop("timeout_seconds", 3600),
        **kw,
    )


class TestItFetchesAndUnpacks:
    def test_it_copies_the_archive_from_the_untrusted_bucket(self):
        script = _script()
        assert "gs://bioaf-untrusted-x/study-1/code.tar.gz" in script

    def test_it_handles_both_archive_shapes_and_a_bare_script(self):
        """A gzipped tar is GitHub's tarball and most supplementary archives; a zip is the journal
        shape; a bare `.R` is a script published on its own."""
        script = _script()
        assert "tar " in script
        assert "unzip" in script

    def test_the_working_directory_is_not_the_output_directory(self):
        """The repository's own files are not results. Syncing the whole checkout back would fill
        the bucket with the paper's source."""
        script = _script()
        assert "/outputs" in script
        assert "/work" in script


class TestDependencies:
    def test_an_r_lockfile_is_restored(self):
        assert "renv" in _script()

    def test_a_python_requirements_file_is_installed(self):
        assert "requirements.txt" in _script()

    def test_a_conda_environment_file_is_applied(self):
        assert "environment.yml" in _script()

    def test_a_failed_install_does_not_stop_the_transcript_being_written(self):
        """The failure IS the finding, and it is only a finding if the log survives to be read."""
        script = _script()
        assert "|| true" in script or "set +e" in script


class TestTheEntryPoint:
    def test_an_r_script_runs_under_rscript(self):
        assert "Rscript" in _script(entry_point="scripts/run.R")

    def test_a_python_script_runs_under_python(self):
        assert "python" in _script(entry_point="scripts/run.py")

    def test_a_notebook_runs_under_nbconvert(self):
        assert "nbconvert" in _script(entry_point="analysis.ipynb")

    def test_the_arguments_are_passed_through(self):
        assert "--counts /data/matrix.tsv" in _script(arguments="--counts /data/matrix.tsv")

    def test_an_entry_point_that_escapes_the_checkout_is_refused(self):
        """The entry point comes from a model reading a file listing. A path that climbs out of the
        checkout would run something we never fetched."""
        from app.exceptions import ValidationError

        with pytest.raises(ValidationError):
            _script(entry_point="../../../bin/sh")


class TestTheTranscript:
    def test_everything_is_captured_to_one_log(self):
        script = _script()
        assert "transcript" in script

    def test_the_log_lands_in_outputs_so_it_survives_the_pod(self):
        script = _script()
        assert "/outputs/" in script and "transcript" in script

    def test_the_run_is_capped_so_a_hang_becomes_an_outcome(self):
        """A hang has to become a finding with a reason rather than a study that ticks forever."""
        assert "timeout 900" in _script(timeout_seconds=900)


class TestTheManifestUsesIt:
    def test_a_fetched_code_spec_no_longer_needs_a_notebook(self):
        """The branch that raised. `notebook_json` is the template arm's input and a fetched
        repository has none."""
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

        adapter = KubernetesNotebookProvider.__new__(KubernetesNotebookProvider)
        spec = {
            "session_type": "headless",
            "session_id": 1,
            "namespace": "bioaf-untrusted",
            "fetched_code": {
                "code_uri": "gs://bioaf-untrusted-x/study-1/code.tar.gz",
                "entry_point": "analysis.R",
                "arguments": "",
            },
            "timeout_seconds": 600,
        }
        # The command is built without a notebook, which is the whole assertion.
        command = adapter._headless_command(spec)
        assert command[0] == "/bin/sh"
        assert "analysis.R" in command[-1]

    def test_the_template_arm_is_untouched(self):
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider

        adapter = KubernetesNotebookProvider.__new__(KubernetesNotebookProvider)
        command = adapter._headless_command(
            {"session_type": "headless", "session_id": 1, "notebook_json": {"cells": []}}
        )
        assert "nbconvert" in command[-1]

    def test_a_headless_spec_with_neither_is_still_refused(self):
        from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
        from app.exceptions import ValidationError

        adapter = KubernetesNotebookProvider.__new__(KubernetesNotebookProvider)
        with pytest.raises(ValidationError):
            adapter._headless_command({"session_type": "headless", "session_id": 1})
