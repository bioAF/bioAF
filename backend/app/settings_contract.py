"""The compute-mode contract and safe settings errors, shared by settings, adapters, and the checker.

Kept apart from ``app.config`` so a caller can catch :class:`SettingsError` from a failed
``import app.config`` (``app.cli.check_settings`` does), and so the parser stays a pure function.

``BIOAF_COMPUTE_MODE`` decides only whether the mode-aware adapters simulate their work (``local``) or
contact their real backends. It does not choose the compute stack (``platform_config.compute_stack``)
or the cloud (``platform_config.cloud_provider``).
"""

from enum import StrEnum

from pydantic import ValidationError

COMPUTE_MODE_SETTING = "BIOAF_COMPUTE_MODE"


class ComputeMode(StrEnum):
    LOCAL = "local"
    KUBERNETES = "kubernetes"
    # Legacy: accepted and preserved, but it neither selects the SLURM stack nor enables SLURM polling.
    SLURM = "slurm"


# Accepted spellings that are not canonical values. ``k8s`` is what production Compose supplied until
# 2026.11 and what existing installs may still set.
COMPUTE_MODE_ALIASES = {"k8s": ComputeMode.KUBERNETES}

ACCEPTED_COMPUTE_MODES = "local, kubernetes (alias k8s), slurm (legacy)"

COMPUTE_MODE_ADVICE = (
    f"{COMPUTE_MODE_SETTING} must be one of: {ACCEPTED_COMPUTE_MODES}. "
    "Set local for development (simulated compute) or kubernetes for real backends."
)


def parse_compute_mode(value: object) -> ComputeMode:
    """The canonical mode for ``value``: surrounding whitespace and case are ignored; nothing else is.

    Raises ValueError naming the setting and the accepted values, never echoing the rejected input.
    """
    if isinstance(value, ComputeMode):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{COMPUTE_MODE_SETTING} is invalid. {COMPUTE_MODE_ADVICE}")
    normalized = value.strip().lower()
    if normalized in COMPUTE_MODE_ALIASES:
        return COMPUTE_MODE_ALIASES[normalized]
    try:
        return ComputeMode(normalized)
    except ValueError:
        problem = "is empty" if not normalized else "is not a recognized value"
        raise ValueError(f"{COMPUTE_MODE_SETTING} {problem}. {COMPUTE_MODE_ADVICE}") from None


def is_simulated(mode: str) -> bool:
    """True when adapters built for ``mode`` simulate their work instead of contacting real backends."""
    return mode == ComputeMode.LOCAL


class SettingsError(Exception):
    """Application settings were rejected. The message is safe to print: it carries no input values."""


def describe_settings_error(exc: ValidationError, env_prefix: str = "BIOAF_") -> str:
    """One line per rejected setting, built from field locations and messages only (never inputs)."""
    lines = ["Application settings are invalid:"]
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        field = ".".join(str(part) for part in error["loc"])
        name = f"{env_prefix}{field}".upper()
        if name == COMPUTE_MODE_SETTING:
            problem = "is not set" if error["type"] == "missing" else "is invalid"
            lines.append(f"  {COMPUTE_MODE_SETTING} {problem}. {COMPUTE_MODE_ADVICE}")
        else:
            lines.append(f"  {name}: {error['msg']}")
    return "\n".join(lines)
