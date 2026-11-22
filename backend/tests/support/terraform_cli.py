"""Fake the Terraform executor's short CLI commands (init, plan, show, output).

The executor runs those through one registered-child seam, ``TerraformExecutor._run_captured``,
whose arguments mirror ``subprocess.run``. ``terraform_cli`` patches that seam with a
``subprocess.run``-shaped fake, so a test describes CLI results the same way it always has.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.terraform_executor import TerraformExecutor

_UNSET = object()


def cli_fake(side_effect=None, return_value=_UNSET):
    async def fake(cmd, **kwargs):
        if side_effect is not None:
            if isinstance(side_effect, BaseException) or (
                isinstance(side_effect, type) and issubclass(side_effect, BaseException)
            ):
                raise side_effect
            return side_effect(cmd, **kwargs)
        return None if return_value is _UNSET else return_value

    return fake


def terraform_cli(side_effect=None, return_value=_UNSET):
    """Patch the captured Terraform commands with ``side_effect(cmd, **kwargs)`` or ``return_value``."""
    return patch.object(TerraformExecutor, "_run_captured", new=cli_fake(side_effect, return_value))
