"""Dependabot pull requests get their changelog entry from a template (.github/workflows/dependabot-changelog.yml).

Dependabot cannot write a file under changes/unreleased/, so the changelog check failed every one of its pull
requests. The workflow renders .github/dependabot-changelog.md from the pull request's title and commits it to the
Dependabot branch through the contents API. It runs on pull_request_target, so it holds a write credential: it must
never check out or run the pull request's code, and the title (Dependabot's text) must stay data.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "dependabot-changelog.yml"
TEMPLATE = REPO / ".github" / "dependabot-changelog.md"
ALL_WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
STEP = "Commit the templated changelog entry"
EXPLICIT_BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]

# A stand-in for gh: it records each call, answers the template read with the real template, and answers the
# entry lookup as present or missing (gh exits 1 on a 404).
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
path = next(a for a in args if a.startswith("repos/"))
method = args[args.index("--method") + 1]
if method == "GET" and path.endswith("/.github/dependabot-changelog.md"):
    sys.stdout.write(open(os.environ["FAKE_TEMPLATE"]).read())
elif method == "GET":
    sys.exit(0 if os.environ.get("FAKE_ENTRY_EXISTS") == "1" else 1)
"""


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _job() -> dict:
    return _workflow()["jobs"]["entry"]


def _step() -> dict:
    return next(s for s in _job()["steps"] if s.get("name") == STEP)


def _run(tmp_path: Path, title: str, head_ref: str, exists: bool = False) -> tuple[subprocess.CompletedProcess, list]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    log.write_text("")
    env = {
        "PATH": f"{bin_dir}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GH_LOG": str(log),
        "FAKE_TEMPLATE": str(TEMPLATE),
        "FAKE_ENTRY_EXISTS": "1" if exists else "0",
        "GH_TOKEN": "pat",
        "REPOSITORY": "bioaf/bioaf",
        "PR_NUMBER": "205",
        "PR_TITLE": title,
        "HEAD_REF": head_ref,
        "BASE_SHA": "a" * 40,
    }
    done = subprocess.run(
        [*EXPLICIT_BASH, "-c", _step()["run"]], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return done, calls


def _field(call: list[str], name: str) -> str:
    return next(
        call[i + 1].split("=", 1)[1] for i, a in enumerate(call) if a == "-f" and call[i + 1].startswith(name + "=")
    )


def _put(calls: list) -> list[str]:
    puts = [c for c in calls if c[c.index("--method") + 1] == "PUT"]
    assert len(puts) == 1, calls
    return puts[0]


# ── what it writes ──


@pytest.mark.parametrize(
    ("title", "head_ref", "bullet"),
    [
        (
            "chore(deps): bump actions/setup-node from 5.0.0 to 7.1.0",
            "dependabot/github_actions/actions/setup-node-7.1.0",
            "- Bump actions/setup-node from 5.0.0 to 7.1.0 (CI workflow actions).",
        ),
        (
            "chore(deps): bump the actions-routine group with 3 updates",
            "dependabot/github_actions/actions-routine-7a2de0ac2e",
            "- Bump the actions-routine group with 3 updates (CI workflow actions).",
        ),
        (
            "chore(deps-dev): bump the frontend-development group in /frontend with 4 updates",
            "dependabot/npm_and_yarn/frontend/frontend-development-1a2b3c",
            "- Bump the frontend-development group in /frontend with 4 updates (frontend).",
        ),
        (
            "chore(deps): bump fastapi from 0.115.0 to 0.116.1 in /backend",
            "dependabot/uv/backend/fastapi-0.116.1",
            "- Bump fastapi from 0.115.0 to 0.116.1 in /backend (backend).",
        ),
    ],
)
def test_the_entry_is_the_template_rendered_from_the_title(tmp_path, title, head_ref, bullet):
    done, calls = _run(tmp_path, title, head_ref)
    assert done.returncode == 0, done.stdout + done.stderr
    put = _put(calls)
    assert put[put.index("--method") + 2] == "repos/bioaf/bioaf/contents/changes/unreleased/dependabot-205.md"
    assert _field(put, "branch") == head_ref
    assert base64.b64decode(_field(put, "content")).decode() == f"### Dependencies\n\n{bullet}\n"


def test_the_template_is_read_from_the_base_commit_not_the_pull_request(tmp_path):
    done, calls = _run(tmp_path, "chore(deps): bump x from 1.0.0 to 1.1.0", "dependabot/uv/backend/x-1.1.0")
    assert done.returncode == 0, done.stdout + done.stderr
    read = next(c for c in calls if c[c.index("--method") + 2].endswith("/.github/dependabot-changelog.md"))
    assert _field(read, "ref") == "a" * 40


def test_an_existing_entry_is_left_alone(tmp_path):
    # The workflow's own commit fires synchronize again; that run, and any later one, changes nothing.
    done, calls = _run(
        tmp_path, "chore(deps): bump x from 1.0.0 to 1.1.0", "dependabot/uv/backend/x-1.1.0", exists=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert [c for c in calls if c[c.index("--method") + 1] == "PUT"] == []


def test_an_unknown_ecosystem_fails_visibly_and_writes_nothing(tmp_path):
    done, calls = _run(tmp_path, "chore(deps): bump x from 1 to 2", "dependabot/docker/x-2")
    assert done.returncode != 0
    assert "::error::" in done.stdout
    assert [c for c in calls if c[c.index("--method") + 1] == "PUT"] == []


@pytest.mark.parametrize(
    "title",
    [
        "chore(deps): bump $(touch {marker}) from 1 to 2",
        "chore(deps): bump `touch {marker}` from 1 to 2",
        "chore(deps): bump x'; touch {marker}; echo ' from 1 to 2",
        "chore(deps): bump & \\1 {{{{area}}}} from 1 to 2",
    ],
)
def test_the_title_is_never_executed_and_lands_verbatim(tmp_path, title):
    marker = tmp_path / "executed"
    title = title.format(marker=marker)
    done, calls = _run(tmp_path, title, "dependabot/uv/backend/x-2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not marker.exists()
    summary = title.removeprefix("chore(deps): b")
    content = base64.b64decode(_field(_put(calls), "content")).decode()
    assert content == f"### Dependencies\n\n- B{summary} (backend).\n"


# ── what it may touch ──


def test_it_runs_only_for_dependabot_pull_requests_from_this_repository():
    workflow = _workflow()
    assert workflow[True] == {
        "pull_request_target": {"branches": ["main"], "types": ["opened", "reopened", "synchronize"]}
    }
    condition = _job()["if"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in condition
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert _job()["concurrency"]["group"] == "dependabot-changelog-${{ github.event.pull_request.number }}"


def test_it_never_checks_out_or_runs_the_pull_request_code():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert [s for s in _job()["steps"] if "uses" in s] == []
    assert "git " not in _step()["run"]


def test_values_reach_the_script_through_env():
    assert _step()["env"] == {
        "GH_TOKEN": "${{ secrets.RELEASE_PAT }}",
        "REPOSITORY": "${{ github.repository }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "PR_TITLE": "${{ github.event.pull_request.title }}",
        "HEAD_REF": "${{ github.event.pull_request.head.ref }}",
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
    }
    assert "${{" not in _step()["run"]
    assert _step()["shell"] == "bash"


def test_the_release_pat_reaches_only_the_release_push_and_this_step():
    holders = [
        (path.name, step.get("name"))
        for path in ALL_WORKFLOWS
        for job in yaml.safe_load(path.read_text())["jobs"].values()
        for step in job.get("steps", [])
        if "secrets.RELEASE_PAT" in json.dumps(step)
    ]
    assert sorted(holders) == [("dependabot-changelog.yml", STEP), ("release.yml", "Commit, tag, and push")]
    assert sum(path.read_text().count("secrets.RELEASE_PAT") for path in ALL_WORKFLOWS) == 2


def test_the_template_is_an_entry_the_release_can_assemble():
    text = TEMPLATE.read_text()
    assert text.startswith("### Dependencies\n\n- ")
    assert "{{summary}}" in text and "{{area}}" in text
