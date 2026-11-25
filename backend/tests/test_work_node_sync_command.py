"""The node-side save command Stop runs as bioaf-sync (plan 03A), executed for real in bash.

Stop saves /outputs, the only location the product promises ("Files in /outputs/ will be synced").
It no longer searches /home for scripts: that search could never read scientists' homes as
bioaf-sync, and hiding its errors let a Stop report success without saving what it looked for
(owner decision, 2026-11-25).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.adapters.work_nodes.node_ssh import sync_outputs_command


def _run(outputs: Path, sync: str) -> int:
    command = sync_outputs_command(sync, outputs=str(outputs))
    return subprocess.run(["bash", "-c", command], env=dict(os.environ)).returncode


def test_outputs_are_synced_and_the_save_succeeds(tmp_path):
    outputs, dest = tmp_path / "outputs", tmp_path / "dest"
    outputs.mkdir()
    dest.mkdir()
    (outputs / "result.csv").write_text("a\n")
    assert _run(outputs, f'cp -R "{outputs}"/. "{dest}"/') == 0
    assert (dest / "result.csv").read_text() == "a\n"


def test_a_failed_output_sync_fails_the_save(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "result.csv").write_text("a\n")
    assert _run(outputs, "false") != 0


def test_an_empty_outputs_directory_is_a_successful_no_op(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    assert _run(outputs, "false") == 0


def test_stop_does_not_search_home_directories():
    command = sync_outputs_command("true")
    assert "/home" not in command
    assert "find " not in command
