"""The stdout format and the shipped Ops Agent pipeline agree (a model, not the agent).

A backend record is written by the real formatter, wrapped in a Docker json-file envelope, and run
through a model of the shipped pipeline: parse the envelope, parse its ``log`` field (kept unchanged
when it is not JSON), copy ``severity``. This proves the two sides name the same fields and that the
time format matches. It does not prove the real agent's parser behaves identically or that entries
reach Cloud Logging; that needs a supported GCE host.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.logging_config import JsonFormatter

CONFIG = Path(__file__).resolve().parents[2] / "docker" / "ops-agent-config.yaml"
# The agent's %L (fractional seconds) is Python's %f here.
FLUENT_TO_PYTHON = {"%L": "%f"}


@pytest.fixture
def pipeline():
    config = yaml.safe_load(CONFIG.read_text())["logging"]
    processors = config["processors"]
    names = config["service"]["pipelines"]["docker"]["processors"]
    return [processors[name] | {"name": name} for name in names]


def _python_format(fluent: str) -> str:
    for src, dst in FLUENT_TO_PYTHON.items():
        fluent = fluent.replace(src, dst)
    return fluent


def _run(pipeline, envelope_line: str) -> dict:
    """The documented behaviour of each processor the shipped config uses."""
    entry: dict = {"jsonPayload": {}, "timestamp": None, "severity": None}
    for step in pipeline:
        if step["type"] == "parse_json":
            field = step.get("field", "message")
            source = envelope_line if step["name"] == "parse_docker_json" else entry["jsonPayload"].get(field)
            try:
                parsed = json.loads(source)
            except (TypeError, ValueError):
                continue  # not JSON: the record is kept unchanged
            if not isinstance(parsed, dict):
                continue
            if step["name"] != "parse_docker_json":
                entry["jsonPayload"].pop(field, None)
            if step.get("time_key") and step["time_key"] in parsed:
                entry["timestamp"] = datetime.strptime(
                    parsed.pop(step["time_key"])[:26] + "Z", _python_format(step["time_format"])
                )
            entry["jsonPayload"].update(parsed)
        elif step["type"] == "modify_fields":
            for destination, spec in step["fields"].items():
                source = spec["copy_from"].removeprefix("jsonPayload.")
                if source in entry["jsonPayload"]:
                    entry[destination] = entry["jsonPayload"][source]
    return entry


def _docker_envelope(line: str) -> str:
    return json.dumps({"log": line + "\n", "stream": "stdout", "time": "2026-11-13T10:00:00.123456789Z"})


def _formatted(record: logging.LogRecord) -> str:
    return JsonFormatter().format(record)


def test_the_pipeline_parses_the_backend_line_out_of_the_docker_envelope(pipeline):
    assert [step["name"] for step in pipeline] == ["parse_docker_json", "parse_bioaf_json", "bioaf_severity"]
    record = logging.LogRecord(
        "bioaf.services.work_dir_reaper", logging.WARNING, __file__, 1, "reap %s", ("x\ny",), None
    )
    record.json_fields = {"run_id": 7}
    line = _formatted(record)
    entry = _run(pipeline, _docker_envelope(line))
    assert entry["severity"] == "WARNING"
    assert entry["jsonPayload"]["logger"] == "bioaf.services.work_dir_reaper"
    assert entry["jsonPayload"]["message"] == "reap x\ny"
    assert entry["jsonPayload"]["fields"] == {"run_id": 7}
    assert "log" not in entry["jsonPayload"]
    assert entry["timestamp"] == datetime.strptime(json.loads(line)["time"], "%Y-%m-%dT%H:%M:%S.%fZ")


def test_an_exception_stays_one_entry_with_its_traceback(pipeline):
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("bioaf.x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    entry = _run(pipeline, _docker_envelope(_formatted(record)))
    assert entry["severity"] == "ERROR"
    assert "ValueError: boom" in entry["jsonPayload"]["exception"]


@pytest.mark.parametrize("text", ["INFO:     Uvicorn running on http://0.0.0.0:8000", "[1] plain text", "{not json"])
def test_non_json_container_output_passes_through_as_the_docker_envelope(pipeline, text):
    entry = _run(pipeline, _docker_envelope(text))
    assert entry["jsonPayload"]["log"] == text + "\n"
    assert entry["jsonPayload"]["stream"] == "stdout"
    assert entry["severity"] is None


def test_every_formatter_severity_is_a_cloud_logging_severity():
    cloud = {"DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"}
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        record = logging.LogRecord("bioaf.x", level, __file__, 1, "m", None, None)
        assert json.loads(_formatted(record))["severity"] in cloud
