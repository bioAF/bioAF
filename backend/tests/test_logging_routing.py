"""Application loggers all reach the configured application sinks.

Every application module logs under the ``bioaf`` hierarchy, so the shared configuration (one stdout
handler, its sanitizer and level) applies to it. Records are read back from the emitted stdout, not
from handler lists.
"""

from __future__ import annotations

import importlib
import json
import logging

import pytest

from app.logging_config import configure_logging, get_app_logger

OUTLIER_MODULES = [
    "app.adapters.kubernetes.connection",
    "app.services.cluster_quota_service",
    "app.services.orphaned_resource_service",
    "app.services.run_output_location_service",
    "app.services.validation_fetch_reaper",
    "app.services.work_dir_reaper",
]


def _records(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_a_module_name_maps_into_the_application_hierarchy():
    assert get_app_logger("app.services.work_dir_reaper").name == "bioaf.services.work_dir_reaper"
    assert get_app_logger("app").name == "bioaf"
    assert get_app_logger("bioaf.email").name == "bioaf.email"
    assert isinstance(get_app_logger("app.x"), logging.Logger)


def test_the_helper_installs_no_handlers():
    logger = get_app_logger("app.services.helper_probe")
    assert logger.handlers == []
    assert logger.propagate is True


@pytest.mark.parametrize("module_name", OUTLIER_MODULES)
def test_each_previously_unrouted_module_reaches_application_stdout(module_name, capsys):
    configure_logging(debug=False)
    module = importlib.import_module(module_name)
    assert module.logger.name.startswith("bioaf.")
    module.logger.info("routing probe from %s", module_name)

    records = [r for r in _records(capsys.readouterr().out) if r["message"].startswith("routing probe")]
    assert len(records) == 1
    assert records[0]["logger"] == module.logger.name
    assert records[0]["severity"] == "INFO"


def test_production_drops_debug_and_keeps_info_and_warning(capsys):
    configure_logging(debug=False)
    log = get_app_logger("app.services.level_probe")
    log.debug("debug line")
    log.info("info line")
    log.warning("warning line")
    messages = [r["message"] for r in _records(capsys.readouterr().out)]
    assert "debug line" not in messages
    assert "info line" in messages and "warning line" in messages


def test_debug_mode_emits_debug_for_existing_and_adopted_loggers(capsys):
    configure_logging(debug=True)
    logging.getLogger("bioaf.email").debug("existing debug")
    get_app_logger("app.services.work_dir_reaper").debug("adopted debug")
    messages = [r["message"] for r in _records(capsys.readouterr().out)]
    assert "existing debug" in messages and "adopted debug" in messages


def test_reconfiguring_changes_the_level_for_every_child(capsys):
    configure_logging(debug=True)
    configure_logging(debug=False)
    get_app_logger("app.services.work_dir_reaper").debug("should not appear")
    logging.getLogger("bioaf.email").debug("nor this")
    assert capsys.readouterr().out == ""


def test_root_and_vendor_loggers_are_left_alone():
    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn.error")
    before = (list(root.handlers), root.level, list(uvicorn.handlers), uvicorn.level, uvicorn.propagate)
    configure_logging(debug=True)
    assert (list(root.handlers), root.level, list(uvicorn.handlers), uvicorn.level, uvicorn.propagate) == before
    assert logging.getLogger("bioaf").propagate is True


class _RawCapture(logging.Handler):
    """No sanitizer: proves exclusion at the source."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def test_a_failed_out_of_cluster_client_build_logs_its_class_not_its_text(monkeypatch):
    """The newly routed K8s connection must not ship credential-handling error text or tracebacks."""
    from app.adapters.kubernetes import connection

    marker = "unregistered-k8s-marker-5e1f"

    class Auth:
        def cluster_endpoint(self, cfg):
            return "https://203.0.113.10"

        def cluster_ca_cert(self, cfg):
            return ""

        def bearer_token(self, cfg):
            raise ValueError(f"token exchange rejected: {marker}")

    conn = connection.GkeConnection(config_keys=[])
    conn._cluster_auth_provider = Auth()
    monkeypatch.setattr(connection.config, "load_incluster_config", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    capture = _RawCapture()
    bioaf = logging.getLogger("bioaf")
    bioaf.addHandler(capture)
    bioaf.setLevel(logging.DEBUG)

    with pytest.raises(ValueError, match=marker):
        conn.get_api_client()

    text = "\n".join(capture.lines)
    assert marker not in text
    assert "Traceback" not in text
    failure = [line for line in capture.lines if "out-of-cluster" in line]
    assert failure and "ValueError" in failure[0]
    assert failure[0].startswith("bioaf.adapters.kubernetes.connection ERROR")


def test_no_application_module_declares_a_logger_outside_the_hierarchy():
    """Guard against new ``app.*`` outliers: a literal logger name must start with ``bioaf``, and a
    module-named logger must come from ``get_app_logger``. Reads source only; imports nothing."""
    import ast
    from pathlib import Path

    import app as app_package

    root = Path(app_package.__file__).resolve().parent
    outliers = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "logging_config.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "getLogger" or not node.args:
                continue
            arg = node.args[0]
            literal_ok = (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and (arg.value == "bioaf" or arg.value.startswith("bioaf."))
            )
            if not literal_ok:
                outliers.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert outliers == [], "use get_app_logger(__name__) or a bioaf.* name: " + ", ".join(outliers)
