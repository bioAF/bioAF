"""Tests for the LLM response parser (ADR-053).

The parser must extract the fenced JSON header, fall back cleanly on any
parsing error, and never lose the body in either case.
"""

from __future__ import annotations

import pytest

from app.services.agent_review_response_parser import (
    PARSE_FAILURE_MARKER,
    parse,
)


def test_well_formed_response_parsed():
    raw = """```json
{
  "severity": "orange",
  "headline": "Two outlier samples",
  "flags": [
    {"title": "Sample S-3", "body": "low coverage", "severity": "orange"}
  ],
  "evidence": ["sample S-3 coverage = 0.4x"]
}
```

Free-text body here.
Goes on for a while.
"""
    result = parse(raw)
    assert result.severity == "orange"
    assert result.headline == "Two outlier samples"
    assert len(result.flags) == 1
    assert result.flags[0]["title"] == "Sample S-3"
    assert result.flags[0]["severity"] == "orange"
    assert result.evidence == ["sample S-3 coverage = 0.4x"]
    assert "Free-text body here" in result.body
    assert result.parse_failure is False


def test_response_without_fence_falls_back_to_unknown():
    raw = "this is just text, no JSON anywhere"
    result = parse(raw)
    assert result.severity == "unknown"
    assert result.headline == PARSE_FAILURE_MARKER
    assert result.body == raw
    assert result.parse_failure is True


def test_malformed_json_falls_back():
    raw = "```json\n{not actually json}\n```\nbody"
    result = parse(raw)
    assert result.severity == "unknown"
    assert result.parse_failure is True


def test_invalid_severity_falls_back_but_keeps_headline():
    raw = """```json
{"severity": "purple", "headline": "Mystery"}
```
body"""
    result = parse(raw)
    assert result.severity == "unknown"
    assert result.headline == "Mystery"
    assert result.parse_failure is True


def test_unfenced_json_block_still_recognized_with_bare_fence():
    raw = """```
{
  "severity": "green",
  "headline": "Looks fine"
}
```
body"""
    result = parse(raw)
    assert result.severity == "green"
    assert result.headline == "Looks fine"
    assert result.parse_failure is False


def test_flags_with_bad_severity_are_unknown():
    raw = """```json
{
  "severity": "red",
  "headline": "Bad run",
  "flags": [{"title": "x", "body": "y", "severity": "rainbow"}]
}
```
body"""
    result = parse(raw)
    assert result.severity == "red"
    assert result.flags[0]["severity"] == "unknown"
    assert result.parse_failure is False


def test_missing_optional_fields_default_to_empty():
    raw = """```json
{
  "severity": "green",
  "headline": "All good"
}
```
"""
    result = parse(raw)
    assert result.flags == []
    assert result.evidence == []


@pytest.mark.parametrize("severity", ["red", "orange", "green"])
def test_each_valid_severity_round_trips(severity):
    raw = f"""```json
{{"severity": "{severity}", "headline": "x"}}
```
body"""
    result = parse(raw)
    assert result.severity == severity
    assert result.parse_failure is False
