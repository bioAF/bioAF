"""The compute-mode contract: one parser for every source of ``BIOAF_COMPUTE_MODE``.

Pure decisions from explicit inputs: no database, environment, or settings construction.
"""

import pytest

from app.settings_contract import ComputeMode, is_simulated, parse_compute_mode


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("local", ComputeMode.LOCAL),
        ("kubernetes", ComputeMode.KUBERNETES),
        ("k8s", ComputeMode.KUBERNETES),
        ("slurm", ComputeMode.SLURM),
        ("LOCAL", ComputeMode.LOCAL),
        ("K8S", ComputeMode.KUBERNETES),
        (" Kubernetes ", ComputeMode.KUBERNETES),
        (" SLURM ", ComputeMode.SLURM),
        ("\tlocal\n", ComputeMode.LOCAL),
        (ComputeMode.KUBERNETES, ComputeMode.KUBERNETES),
    ],
)
def test_known_values_normalize_to_their_canonical_mode(raw, expected):
    assert parse_compute_mode(raw) is expected


@pytest.mark.parametrize("raw", ["", "   ", "typo", "production", "gce", "aws", "k 8s", "kube", None, 1])
def test_unknown_or_blank_values_are_rejected_with_the_setting_and_accepted_values(raw):
    with pytest.raises(ValueError) as exc:
        parse_compute_mode(raw)
    message = str(exc.value)
    assert "BIOAF_COMPUTE_MODE" in message
    for accepted in ("local", "kubernetes", "k8s", "slurm"):
        assert accepted in message


def test_the_rejection_never_echoes_the_rejected_value():
    with pytest.raises(ValueError) as exc:
        parse_compute_mode("SENTINEL-not-a-mode")
    assert "SENTINEL" not in str(exc.value)


def test_only_local_mode_simulates():
    assert is_simulated(ComputeMode.LOCAL)
    assert not is_simulated(ComputeMode.KUBERNETES)
    assert not is_simulated(ComputeMode.SLURM)


def test_canonical_values_are_plain_strings():
    # Logged and compared as text (startup diagnostics, update status), so the value is the spelling.
    assert [m.value for m in ComputeMode] == ["local", "kubernetes", "slurm"]
    assert f"{ComputeMode.KUBERNETES}" == "kubernetes"
