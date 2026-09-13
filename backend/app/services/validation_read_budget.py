"""plan_8_1 section 1.1: the measured output budget of each model call a paper read makes.

Studies 42 and 43 were cut off at 4096 output tokens, the Anthropic client's fixed cap, after one deploy
grew the extraction's answer twice over. Nothing caught it: unit tests use canned answers, so answer
size never mattered.

**A budget is a measurement, not a guess.** Each call a read makes (the extraction; stage 2 adds the
finding inventory) has a record under ``read_measurements/``: the fingerprint of the prompt and schema it
was measured under, the model, each paper's output tokens and elapsed time, the budget chosen with its
headroom, and the date. ``tests/test_read_budget.py`` pins the current fingerprint to the record, so a
change to the prompt or the schema fails until someone measures again. The guard detects a stale
measurement; it cannot promise that every paper fits, and bounded recovery covers the rest.

**A model the record does not name still reads.** Its provenance says "budget not measured for this
model", because the budget it runs under is somebody else's measurement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

EXTRACTION = "extraction"
# plan_8_1 section 2.2: the finding inventory's own call, measured and fingerprinted on its own.
INVENTORY = "inventory"

_RECORDS = Path(__file__).resolve().parent / "read_measurements"

# section 1.2: a truncated answer is retried once with this multiple of its budget.
RECOVERY_MULTIPLE = 2
# Of the time a measured rate says an answer could take, how much a recovery budget may plan to use.
TIME_SAFETY = 0.8

NOT_MEASURED_NOTE = "budget not measured for this model"

# The documented output maximum of the models bioAF has been configured with, by id prefix (the most
# specific prefix wins). A recovery budget never exceeds it: a request over a model's maximum is
# refused outright, which is a worse failure than the truncation it was retrying.
_OUTPUT_LIMITS: tuple[tuple[str, int], ...] = (
    ("claude-fable-5", 128000),
    ("claude-mythos-5", 128000),
    ("claude-opus-5", 128000),
    ("claude-sonnet-5", 128000),
    ("claude-opus-4-8", 128000),
    ("claude-opus-4-7", 128000),
    ("claude-opus-4-6", 128000),
    ("claude-sonnet-4-6", 128000),
    ("claude-opus-4-5", 64000),
    ("claude-sonnet-4-5", 64000),
    ("claude-haiku-4-5", 64000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4", 32000),
    ("claude-sonnet-4", 64000),
    ("gpt-5", 128000),
    ("gpt-4.1", 32768),
    ("gpt-4o", 16384),
    ("gemini-2.5", 65536),
)
# A model bioAF knows nothing about gets no more than this, which every current provider accepts.
DEFAULT_OUTPUT_LIMIT = 16384


def fingerprint(text: str) -> str:
    """The fingerprint a measurement is pinned to. Any change to the text, of any length, changes it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extraction_fingerprint() -> str:
    """The extraction's system prompt, which carries its schema, as the record pins it."""
    from app.services.validation_extraction_service import build_extraction_prompt

    system, _ = build_extraction_prompt("")
    return fingerprint(system)


def inventory_fingerprint() -> str:
    """The inventory call's system prompt, which carries its schema, as its record pins it."""
    from app.services.validation_inventory_stage import inventory_fingerprint as _fingerprint

    return _fingerprint()


@lru_cache(maxsize=None)
def _read(call: str) -> str:
    return (_RECORDS / f"{call}.json").read_text(encoding="utf-8")


def load_record(call: str) -> dict:
    """The measurement record for one call a read makes."""
    return json.loads(_read(call))


def model_output_limit(model: str | None) -> int:
    """The model's documented output maximum, or the conservative default for a model not listed."""
    name = (model or "").strip().lower()
    for prefix, limit in sorted(_OUTPUT_LIMITS, key=lambda row: len(row[0]), reverse=True):
        if name.startswith(prefix):
            return limit
    return DEFAULT_OUTPUT_LIMIT


@dataclass(frozen=True)
class Budget:
    """The budget one call runs under, and whether the model it runs on was measured."""

    call: str
    max_tokens: int
    measured: bool
    note: str | None
    fingerprint: str | None

    def provenance(self) -> dict:
        return {
            "call": self.call,
            "max_tokens": self.max_tokens,
            "measured": self.measured,
            "note": self.note,
            "record_fingerprint": self.fingerprint,
        }


def budget_for(call: str, model: str | None) -> Budget:
    """The budget ``call`` runs under on ``model``: the record's chosen budget, never above the model's
    maximum. A model the record does not name runs under it too, and says so."""
    record = load_record(call)
    measured = (model or "") in (record.get("models") or {})
    chosen = min(int(record["chosen_budget"]), model_output_limit(model))
    return Budget(
        call=call,
        max_tokens=chosen,
        measured=measured,
        note=None if measured else NOT_MEASURED_NOTE,
        fingerprint=record.get("fingerprint"),
    )


def _deadline_seconds(provider: str | None) -> float:
    """How long one answer may take on this provider: the streamed deadline where the call streams,
    else the client's read timeout."""
    from app.services.llm_provider_clients import anthropic_client, google_client, openai_client

    if provider == "anthropic":
        return anthropic_client.STREAM_DEADLINE_SECONDS
    client = {"openai": openai_client, "google": google_client}.get(provider or "")
    timeout = getattr(getattr(client, "_TIMEOUT", None), "read", None)
    return float(timeout) if timeout else anthropic_client.STREAM_DEADLINE_SECONDS


def recovery_budget(current: int, *, model: str | None, provider: str | None, record: dict | None = None) -> int | None:
    """section 1.2: the larger budget a truncated answer is retried with, or None when there is none.

    A fixed multiple of the budget that was cut off, capped by the model's documented maximum and by
    how many tokens the model's measured rate can produce before the call's deadline. When the cap
    leaves no more room than the answer already had, there is no recovery: the same budget would
    truncate the same way.
    """
    record = record if record is not None else {}
    larger = min(current * RECOVERY_MULTIPLE, model_output_limit(model))
    rate = ((record.get("models") or {}).get(model or "") or {}).get("tokens_per_second")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
        larger = min(larger, int(rate * _deadline_seconds(provider) * TIME_SAFETY))
    return larger if larger > current else None
