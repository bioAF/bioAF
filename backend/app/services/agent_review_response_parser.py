"""Parser for LLM responses to standardized review prompts (ADR-053).

Every provider is instructed to respond with a fenced JSON block at the top of
the response (severity, headline, flags, evidence), followed by a free-text
body. This parser pulls that header out and falls back cleanly when the model
ignores or mangles it.

Return shape:
    ParsedResponse(severity, headline, flags, evidence, body, parse_failure)

On parse failure: severity = 'unknown', headline = a parse-failure marker,
body = the full raw response, parse_failure = True.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

VALID_SEVERITIES = {"red", "orange", "green"}
PARSE_FAILURE_MARKER = "Could not parse LLM header"


@dataclass
class ParsedResponse:
    severity: str
    headline: str
    flags: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    body: str = ""
    parse_failure: bool = False


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse(response_text: str) -> ParsedResponse:
    match = _FENCED_JSON_RE.search(response_text)
    if match is None:
        return ParsedResponse(
            severity="unknown",
            headline=PARSE_FAILURE_MARKER,
            body=response_text,
            parse_failure=True,
        )

    raw = match.group(1)
    try:
        header = json.loads(raw)
    except json.JSONDecodeError:
        return ParsedResponse(
            severity="unknown",
            headline=PARSE_FAILURE_MARKER,
            body=response_text,
            parse_failure=True,
        )

    if not isinstance(header, dict):
        return ParsedResponse(
            severity="unknown",
            headline=PARSE_FAILURE_MARKER,
            body=response_text,
            parse_failure=True,
        )

    severity = header.get("severity")
    if severity not in VALID_SEVERITIES:
        return ParsedResponse(
            severity="unknown",
            headline=str(header.get("headline") or PARSE_FAILURE_MARKER),
            body=response_text,
            parse_failure=True,
        )

    flags_in = header.get("flags") or []
    flags: list[dict[str, Any]] = []
    if isinstance(flags_in, list):
        for f in flags_in:
            if isinstance(f, dict):
                flags.append(
                    {
                        "title": str(f.get("title", "")),
                        "body": str(f.get("body", "")),
                        "severity": f.get("severity") if f.get("severity") in VALID_SEVERITIES else "unknown",
                    }
                )

    evidence_in = header.get("evidence") or []
    evidence: list[str] = [str(e) for e in evidence_in] if isinstance(evidence_in, list) else []

    body = response_text[match.end() :].lstrip("\n")

    return ParsedResponse(
        severity=severity,
        headline=str(header.get("headline", "")),
        flags=flags,
        evidence=evidence,
        body=body,
        parse_failure=False,
    )
