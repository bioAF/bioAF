"""The node-side save command Stop runs as bioaf-sync (plan 03A), executed for real in bash.

Found live (2026-11-25): bioaf-sync cannot enter users' 750 home directories, so ``find /home``
exited 1 on every default node and every Stop ended in Stop failed, although the outputs were saved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.adapters.work_nodes.node_ssh import sync_outputs_command

COPY_TO = 'for f do cp "$f" "$DEST"/ || exit 1; done'


def _run(tmp_path: Path, home: Path, outputs: Path, *, sync: str = "true") -> int:
    command = sync_outputs_command(sync, f"'{COPY_TO}'", home=str(home), outputs=str(outputs))
    return subprocess.run(["bash", "-c", command], env={**os.environ, "DEST": str(tmp_path / "dest")}).returncode


@pytest.fixture
def tree(tmp_path):
    home, outputs, dest = tmp_path / "home", tmp_path / "outputs", tmp_path / "dest"
    for d in (home / "sci" / "work", outputs, dest):
        d.mkdir(parents=True)
    (home / "sci" / "work" / "analysis.py").write_text("print(1)\n")
    locked = home / "other"
    locked.mkdir()
    (locked / "secret.py").write_text("x\n")
    locked.chmod(0o000)
    yield home, outputs, dest
    locked.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_an_unreadable_home_directory_does_not_fail_the_save(tmp_path, tree):
    home, outputs, dest = tree
    assert _run(tmp_path, home, outputs) == 0
    assert (dest / "analysis.py").exists()


def test_a_failed_copy_of_a_found_file_fails_the_save(tmp_path, tree):
    home, outputs, dest = tree
    dest.rmdir()  # cp into a missing directory fails
    assert _run(tmp_path, home, outputs) != 0


def test_a_failed_output_sync_fails_the_save(tmp_path, tree):
    home, outputs, _ = tree
    (outputs / "result.csv").write_text("a\n")
    assert _run(tmp_path, home, outputs, sync="false") != 0


def test_nothing_to_save_succeeds(tmp_path):
    home, outputs = tmp_path / "home", tmp_path / "outputs"
    home.mkdir()
    outputs.mkdir()
    assert _run(tmp_path, home, outputs) == 0
