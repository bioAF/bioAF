"""Build a mode-aware adapter in an explicit compute mode and prove it took that branch.

The ``mode_adapter_factory`` fixture (tests/conftest.py) returns :func:`build_mode_adapter`. Tests of
real-backend behavior construct through it instead of exporting BIOAF_COMPUTE_MODE or writing a
private attribute, so a test can never silently run against the simulation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from app.settings_contract import ComputeMode, is_simulated

T = TypeVar("T")


def build_mode_adapter(provider_cls: Callable[..., T], *args: Any, mode: ComputeMode, **kwargs: Any) -> T:
    adapter = provider_cls(*args, mode=mode, **kwargs)
    expected = is_simulated(mode)
    actual = adapter.is_local  # type: ignore[attr-defined]
    assert actual is expected, (
        f"{getattr(provider_cls, '__name__', provider_cls)} built with mode={mode.value} reports is_local={actual}; "
        f"the requested {'local' if expected else 'real'} branch was not selected"
    )
    return adapter
