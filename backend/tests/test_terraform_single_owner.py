"""Arch guard: the Terraform CLI is invoked only by TerraformExecutor.

After ADR-066, TerraformExecutor is the single Terraform execution owner. A second
runner (like the deleted TerraformService) reintroduces the "who runs terraform?"
ambiguity and an unsynchronized lock. This guard fails if any other backend module
shells out to the terraform binary.
"""

import pathlib
import re

# Matches a subprocess command list that runs the terraform binary, e.g.
# ["terraform", "init", ...] or ["terraform", "apply", ...]. A plain string
# "terraform" used as a directory name (gitops repo layout) does not match.
_TERRAFORM_CLI = re.compile(
    r'"terraform",\s*"(init|plan|apply|destroy|output|show|validate|fmt|import|state|version)"'
)

_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
_ALLOWED = {"terraform_executor.py"}


def test_terraform_cli_invoked_only_by_executor():
    offenders = [
        str(path.relative_to(_APP_DIR))
        for path in _APP_DIR.rglob("*.py")
        if path.name not in _ALLOWED and _TERRAFORM_CLI.search(path.read_text())
    ]
    assert not offenders, f"Terraform CLI invoked outside TerraformExecutor (see ADR-066): {offenders}"
