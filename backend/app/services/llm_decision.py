"""plan_7 step 14a: one shape for asking a model to decide something.

Six modules independently declared

    _FENCED_JSON_RE = re.compile(r"```(?:json)?\\s*(\\{.*\\})\\s*```", re.DOTALL)

and then, each in its own way, ran ``json.loads`` on the group, clamped a confidence, and returned a
hardcoded empty on failure. Thirty to forty lines, six times, grown one caller at a time and never
designed as a set. plan_7's remaining steps add four more callers.

**Why this exists is attribution, not DRY.** ``bind_claims`` records which model decided, why, how
confident it was and whether it was model, human or alias table. The other four decisions record
none of that, and no decision anywhere could record a refusal. For a feature whose premise is that
an AI decision which cannot be attributed is a defect, that is the defect.

**What this owns:** transport, the fence, the parse, the confidence clamp, the membership check, and
a structured outcome. **What the caller keeps:** the shape of its own answer and its post-validation
transforms. ``bind_claims`` keeps its row expansion, because "a short answer cannot silently drop a
claim" is its own guarantee and serving it here would add a second return mode used by one caller.

**Nothing is swallowed.** "Never raise" stays, in the sense that asking a model for help still
cannot kill a study, but it stops meaning "never explain". Every call returns
``{outcome, data, reason, model}`` with ``outcome`` one of ``ok | refusal | unreachable | internal |
unparseable``, and the caller decides degrade versus halt. This dissolves the ``None`` versus ``[]``
drift: those meant "the call failed" and "answered, nothing to say" and were indistinguishable at
the call site.

**No retry concept.** Transport retry for 429 and 5xx lives in the provider clients, below this.
The semantic re-ask (``previous=``) stays with ``bind_claims``'s caller, because the trigger is
domain logic: a whole paper binding nothing is worth a second look and this layer cannot know that.

ADR-053 already owns the call (``client.submit``), the family (``get_client``) and the model
(``get_for_feature``). None of that is reimplemented here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.llm_provider_clients import ProviderError

logger = logging.getLogger("bioaf.llm_decision")

OUTCOME_OK = "ok"
OUTCOME_REFUSAL = "refusal"
OUTCOME_UNREACHABLE = "unreachable"
OUTCOME_INTERNAL = "internal"
OUTCOME_UNPARSEABLE = "unparseable"
# change_7.2 section 7: an answer cut off at the token limit is its own event. It is not a
# refusal, not a transport failure, and not a badly formatted answer, and calling it any of
# those sends whoever reads the report to the wrong remedy.
OUTCOME_TRUNCATED = "truncated"

# How much of an unreadable answer reaches the log. It was 400 characters, which is not enough to
# attribute a failure to a cause: no unparseable response in either of the owner's runs could be
# explained afterwards. The answer does NOT go on the issue record, because that is what a user
# reads on screen and the repo's rule is plain language there and technical detail in the logs.
_LOG_ANSWER_CHARS = 20000

OUTCOMES = (
    OUTCOME_OK,
    OUTCOME_REFUSAL,
    OUTCOME_UNREACHABLE,
    OUTCOME_INTERNAL,
    OUTCOME_UNPARSEABLE,
    OUTCOME_TRUNCATED,
)

# What each provider failure means to a person. `refusal` names the model so an admin can request an
# account exception; the reach failures say bioAF could not get to the LLM; everything else says
# bioAF hit an internal error. Real detail goes to the logs (`frontend/src/lib/errorReporting.ts`).
_ERROR_CLASS_OUTCOMES = {
    "refusal": OUTCOME_REFUSAL,
    "auth": OUTCOME_UNREACHABLE,
    "rate_limit": OUTCOME_UNREACHABLE,
    "transport": OUTCOME_UNREACHABLE,
    "server": OUTCOME_UNREACHABLE,
    "parse": OUTCOME_UNPARSEABLE,
    "truncated": OUTCOME_TRUNCATED,
}

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


def fenced_json(response_text: str | None) -> dict | None:
    """The JSON object in a model's answer, or None when there is not one.

    Tries the fenced block first, then a bare object: models drop the fence often enough that
    refusing on it would throw away good answers, and every caller here asks for an object, so a
    top-level array is as unusable as prose.
    """
    text = response_text or ""
    for pattern in (_FENCED_JSON_RE, _BARE_JSON_RE):
        match = pattern.search(text)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def confidence_of(value) -> float:
    """A confidence that is not a number in 0-1 is no confidence at all, so it reads as 0.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


@dataclass
class Decision:
    """One answer from a model, or one honest account of why there is not one."""

    outcome: str
    data: dict = field(default_factory=dict)
    reason: str = ""
    model: str | None = None
    intent: str = ""
    text: str = ""
    allowed: tuple[str, ...] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK

    def member(self, value) -> bool:
        """Whether ``value`` is in the allow-list. Always True when there is no list.

        Lent to callers whose answer has a shape this module does not own: ``resolve_columns``
        answers with a mapping whose VALUES are the closed set, and validates its own role keys in
        about three lines. Keys-as-a-closed-set is the rare case and does not get to shape the
        common one.
        """
        if self.allowed is None:
            return True
        return isinstance(value, str) and value.strip() in self.allowed

    def choice(self, key: str) -> str | None:
        """The top-level string at ``key`` when it is allowed, else None with a note saying why.

        An invented value is the failure this check exists to remove: it would persist as a decision
        and then be acted on as though the thing it names existed.
        """
        raw = self.data.get(key)
        if not isinstance(raw, str) or not raw.strip():
            return None
        value = raw.strip()
        if not self.member(value):
            self.notes.append(f"ignored {value}: not one of the values this decision allows")
            return None
        return value

    def confidence(self, key: str = "confidence") -> float:
        return confidence_of(self.data.get(key))

    def as_issue(self, *, impact: str) -> dict | None:
        """One row for the study's issues section, or None when nothing went wrong.

        ``impact`` is the caller's to state, because only the caller knows whether it fell back and
        carried on (``degraded``) or produced nothing (``blocked``). Without that distinction the
        section cries wolf and users learn to ignore it.
        """
        if self.ok:
            return None
        return {
            "step": self.intent,
            "outcome": self.outcome,
            "impact": impact,
            "message": self.reason,
            "model": self.model,
            "at": datetime.now(timezone.utc).isoformat(),
        }


def _failure_reason(outcome: str, *, intent: str, model: str | None) -> str:
    if outcome == OUTCOME_REFUSAL:
        return (
            f"The model {model or 'configured for this feature'} declined to answer while {intent}. "
            "An administrator may need to request an exception for this account."
        )
    if outcome == OUTCOME_UNREACHABLE:
        return f"bioAF could not reach the language model while {intent}."
    if outcome == OUTCOME_UNPARSEABLE:
        return f"The model's answer while {intent} was not in the format bioAF asked for."
    if outcome == OUTCOME_TRUNCATED:
        return (
            f"The model's answer while {intent} was cut off at its token limit before it finished, "
            "so the part bioAF received could not be read as a complete answer."
        )
    return f"bioAF hit an internal error while {intent}."


async def decide(
    *,
    intent: str,
    system: str,
    payload: str,
    client,
    model: str,
    api_key: str | None,
    allowed: Iterable[str] | None = None,
) -> Decision:
    """Ask a model to decide something, and always return an account of what happened.

    ``intent`` is the step in the user's language ("binding the paper's claims"), not the function
    name: it is what the report shows and what every failure sentence is built from.

    ``allowed`` is optional and constrains VALUES. Absent means the model may answer anything, which
    is what lets the extractor and the generated-analysis arm use this function rather than fork it.
    """
    allow = tuple(str(a) for a in allowed) if allowed is not None else None

    def _failed(outcome: str, detail: str, *, full_text: str = "") -> Decision:
        logger.warning("llm decision failed (%s) while %s: %s", outcome, intent, detail)
        return Decision(
            outcome=outcome,
            reason=_failure_reason(outcome, intent=intent, model=model),
            model=model,
            intent=intent,
            allowed=allow,
            # Kept on the decision so the issue record carries the answer that could not be read.
            # A cause that is only in a truncated log line cannot be established later.
            text=full_text,
        )

    try:
        text = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    except ProviderError as exc:
        return _failed(_ERROR_CLASS_OUTCOMES.get(exc.error_class, OUTCOME_INTERNAL), f"{exc.error_class}: {exc}")
    except Exception as exc:  # noqa: BLE001 - asking a model for help cannot be allowed to fail a study
        logger.exception("llm decision raised while %s", intent)
        return _failed(OUTCOME_INTERNAL, str(exc))

    data = fenced_json(text)
    if data is None:
        # change_7.2 section 7: the whole answer, not 400 characters of it. No unparseable response
        # in either of the owner's runs could be attributed to a cause, because the evidence needed
        # to attribute it had already been thrown away by the time anyone read the log.
        return _failed(OUTCOME_UNPARSEABLE, (text or "")[:_LOG_ANSWER_CHARS], full_text=text or "")

    if "confidence" in data:
        data["confidence"] = confidence_of(data.get("confidence"))
    return Decision(
        outcome=OUTCOME_OK,
        data=data,
        model=model,
        intent=intent,
        text=text or "",
        allowed=allow,
        reason=str(data.get("reason") or "").strip(),
    )
