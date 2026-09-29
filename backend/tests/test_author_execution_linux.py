"""Opt-in Linux control executing held source with the production runner script.

BIOAF_SANDBOX_TEST_IMAGE selects a local image with Python and bubblewrap. Docker options
in BIOAF_SANDBOX_TEST_DOCKER_ARGS may enable namespaces for the local test daemon.
These test options never change the production pod policy.
"""

import json
import os
import shlex
import subprocess

import pytest

from app.adapters.notebooks.kubernetes import build_fetched_code_script
from app.services.validation_code_followup import compare_author_outputs, invocation_outcome
from app.services.validation_environment_check import _archive

IMAGE = os.environ.get("BIOAF_SANDBOX_TEST_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Requires an explicitly selected local Linux sandbox test image")


@pytest.mark.parametrize("case", ["agreement", "disagreement", "defective", "missing_input"])
def test_actual_author_execution_and_runtime_network_denial(tmp_path, case):
    source = """import json, socket
from pathlib import Path
try:
    socket.create_connection(('198.18.0.1', 443), timeout=1)
except OSError:
    print('NETWORK_DENIED_CONFIRMED')
else:
    raise RuntimeError('Runtime network was available')
count = sum(json.loads(Path('/data/counts.json').read_text()))
Path('/outputs/result.json').write_text(json.dumps({'peak_count': count}))
"""
    if case == "defective":
        source = "raise ValueError('intentional defective implementation')\n"
    if case == "missing_input":
        source = "open('/data/missing.csv').read()\n"
    (tmp_path / "code.tar.gz").write_bytes(_archive({"bundle/analysis.py": source}))
    (tmp_path / "counts.json").write_text(json.dumps([20, 22] if case != "disagreement" else [40, 44]))
    script = build_fetched_code_script(
        {
            "code_uri": "/fixture/code.tar.gz",
            "entry_point": "analysis.py",
            "execution_contract": {
                "language": "python",
                "network": "install_only",
                "working_directory": "/work",
            },
        },
        copy_in="cp {uri} {local}",
        auth="true",
        timeout_seconds=20,
    )
    (tmp_path / "run.sh").write_text(script)
    command = [
        "docker",
        "run",
        "--rm",
        *shlex.split(os.environ.get("BIOAF_SANDBOX_TEST_DOCKER_ARGS", "")),
        "--cpus=2",
        "--memory=512m",
        "--mount",
        f"type=bind,src={tmp_path},dst=/fixture,readonly",
        "--mount",
        f"type=bind,src={tmp_path},dst=/data,readonly",
        "--entrypoint",
        "/bin/sh",
        IMAGE,
        "-c",
        "sh /fixture/run.sh; result=$?; cat /outputs/transcript.txt; "
        "test ! -f /outputs/result.json || cat /outputs/result.json; exit $result",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=45)
    transcript = completed.stdout + completed.stderr
    outcome = invocation_outcome(transcript, compute_status="completed" if completed.returncode == 0 else "failed")
    expected = {
        "agreement": "succeeded",
        "disagreement": "succeeded",
        "defective": "failed",
        "missing_input": "blocked",
    }
    assert outcome["status"] == expected[case], transcript
    if case in ("agreement", "disagreement"):
        assert "NETWORK_DENIED_CONFIRMED" in transcript
        output = next(line for line in transcript.splitlines() if line.startswith('{"peak_count"'))
        comparison = compare_author_outputs(
            [{"path": "result.json", "text": output}],
            {"claims": [{"metric_key": "peak_count", "claimed_value": 42, "tolerance": 0.05}]},
            {},
            {},
        )
        assert comparison[0]["within_tolerance"] is (case == "agreement")
