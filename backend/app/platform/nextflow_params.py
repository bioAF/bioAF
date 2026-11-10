"""Which pipeline parameters a launch may carry, and how one becomes a Nextflow argument.

Shared by the launch (which refuses before a run exists) and the compute adapter's renderer (which
checks again, because typed, legacy and replayed specs reach it without passing the launch).

Each parameter becomes one ``--key=value`` argument. Checked against ``nextflow/nextflow:25.10.4``:
in the two-argument form ``--key value`` a value of ``--outdir x`` becomes a second parameter and a
value of ``-x`` aborts the run, while ``--key=value`` keeps any value as the parameter's data and
still lets Nextflow type ``true``/``5``/``-5`` as it always has.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.exceptions import ValidationError

# A Nextflow parameter name: no leading '-', no whitespace, no '=', no shell syntax. Dots address
# nested params and hyphens are accepted by Nextflow (it camel-cases them); every catalog default
# fits this.
_PARAM_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")

# Set by the platform for every run: the samplesheet it built, and the run's authorized output
# location. A caller value for either would replace what the platform reviewed or authorized.
PLATFORM_OWNED_PARAMS = ("input", "outdir")

# bioAF carriers that ride in the parameters but are not Nextflow parameters ("accessions" is
# fetchngs's ids file, materialized into --input by the sheet).
INTERNAL_PARAMS = frozenset({"fusion_enabled", "accessions"})


def refuse_malformed_parameter_names(parameters: Mapping) -> None:
    bad = [k for k in parameters if not isinstance(k, str) or not _PARAM_KEY.fullmatch(k)]
    if bad:
        raise ValidationError(
            "Pipeline parameter names must start with a letter or '_' and contain only letters, digits, "
            "'_', '.' or '-'",
            details={"parameters": sorted(repr(k) for k in bad)},
        )


def refuse_unlaunchable_parameters(parameters: Mapping) -> None:
    """The launch's rule: well-formed names, and nothing the platform sets itself."""
    refuse_malformed_parameter_names(parameters)
    owned = [k for k in PLATFORM_OWNED_PARAMS if k in parameters]
    if owned:
        raise ValidationError(
            "These parameters are set by bioAF for every run and cannot be supplied: " + ", ".join(owned),
            details={"parameters": owned},
        )


def parameter_argument(key: str, value: object) -> str:
    """One parameter as one Nextflow argument; ``str(value)`` is what Nextflow has always been given."""
    return f"--{key}={value}"
