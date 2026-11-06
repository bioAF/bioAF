"""Opt-in seeded shuffle of the collected test order (plan 4, stage 6).

``--shuffle-seed=N`` (or ``BIOAF_TEST_SHUFFLE_SEED=N``) shuffles collection
with ``random.Random(N)``; ``--shuffle-seed=random`` picks a seed. The
controller decides the seed and hands it to every xdist worker, so all workers
collect the same order (xdist refuses to run otherwise). Execution order under
xdist is still concurrent, so the record names the worker count and scheduler.

``--shuffle-record=PATH`` writes the seed, worker count, scheduler and the
collected node ids in order. Replay a failure with the recorded seed.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

import pytest

_STATE: dict[str, Any] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shuffle-seed", default=os.environ.get("BIOAF_TEST_SHUFFLE_SEED") or None)
    parser.addoption("--shuffle-record", default=None, help="write the seed and collected order here")


def _write_record(config: pytest.Config, nodeids: list[str]) -> None:
    record = config.getoption("--shuffle-record")
    if not record or _STATE.get("seed") is None or _STATE.get("recorded"):
        return
    _STATE["recorded"] = True
    Path(record).write_text(
        json.dumps(
            {
                "seed": _STATE["seed"],
                "workers": getattr(config.option, "numprocesses", None),
                "dist": getattr(config.option, "dist", None),
                "collected": nodeids,
            },
            indent=1,
        )
    )


class _XdistHooks:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_configure_node(self, node: Any) -> None:
        node.workerinput["bioaf_shuffle_seed"] = _STATE.get("seed")

    def pytest_xdist_node_collection_finished(self, node: Any, ids: list[str]) -> None:
        # Workers collect; the controller records the (identical) order the first one reports.
        _write_record(self.config, list(ids))


def pytest_configure(config: pytest.Config) -> None:
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        _STATE["seed"] = workerinput.get("bioaf_shuffle_seed")
        return
    raw = config.getoption("--shuffle-seed")
    if raw is None:
        _STATE["seed"] = None
    elif raw == "random":
        _STATE["seed"] = random.SystemRandom().randrange(1, 2**31)
    else:
        _STATE["seed"] = int(raw)
    if config.pluginmanager.hasplugin("xdist"):
        config.pluginmanager.register(_XdistHooks(config), "bioaf_shuffle_xdist")


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list[pytest.Item]) -> None:
    seed = _STATE.get("seed")
    if seed is None:
        return
    random.Random(seed).shuffle(items)
    if getattr(config, "workerinput", None) is None:
        _write_record(config, [item.nodeid for item in items])


def pytest_report_header(config: pytest.Config) -> str | None:
    seed = _STATE.get("seed")
    return f"bioaf shuffle seed: {seed}" if seed is not None else None
