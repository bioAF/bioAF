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
import time
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
# plan_8_1 section 1.1: an answer that did not finish within bioAF's time limit. A budget raised for a
# long answer can run past the limit, and that is a timeout, never a truncation.
OUTCOME_TIMED_OUT = "timed_out"
# plan_8_3 stage 6: an answer that arrived whole and did not satisfy the caller's schema, twice. It is
# not a truncation (nothing was cut off), not unparseable (it was JSON) and not a refusal, and the three
# send whoever reads the report to different remedies.
OUTCOME_SCHEMA_REJECTED = "schema_rejected"
# plan_8_3 stage 6: a fact about the ACCOUNT, not about reaching the provider. An exhausted credit
# balance, an exhausted quota and a model the account is not entitled to are three things an
# administrator can act on, and they arrived as "bioAF could not reach the language model": study 57's
# whole finding inventory was discarded on one of them. No retry, semantic or otherwise, can succeed.
OUTCOME_ACCOUNT = "account"

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
    OUTCOME_TIMED_OUT,
    OUTCOME_SCHEMA_REJECTED,
    OUTCOME_ACCOUNT,
)

# plan_8_3 stage 6: the failures one more ask can plausibly fix. A transport failure, a refusal and an
# internal error are not among them: asking the same question again spends money to fail the same way.
SEMANTIC_FAILURES = (OUTCOME_TRUNCATED, OUTCOME_UNPARSEABLE, OUTCOME_SCHEMA_REJECTED)

# What each provider failure means to a person. `refusal` names the model so an admin can request an
# account exception; the reach failures say bioAF could not get to the LLM; everything else says
# bioAF hit an internal error. Real detail goes to the logs (`frontend/src/lib/errorReporting.ts`).
_ERROR_CLASS_OUTCOMES = {
    "refusal": OUTCOME_REFUSAL,
    "auth": OUTCOME_UNREACHABLE,
    "rate_limit": OUTCOME_UNREACHABLE,
    "transport": OUTCOME_UNREACHABLE,
    "server": OUTCOME_UNREACHABLE,
    "account": OUTCOME_ACCOUNT,
    "parse": OUTCOME_UNPARSEABLE,
    "truncated": OUTCOME_TRUNCATED,
    "timeout": OUTCOME_TIMED_OUT,
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
    # plan_8_1 section 1.1: what the call used, as the provider reported it (None where it did not),
    # the budget it was given, and how long it took. A read records these on its provenance.
    output_tokens: int | None = None
    stop_reason: str | None = None
    max_tokens: int | None = None
    elapsed_seconds: float | None = None
    # plan_8_3 stage 6: the audit entry this call ran under, and one row per attempt it took.
    purpose: str | None = None
    attempts: list[dict] = field(default_factory=list)

    def usage(self) -> dict:
        """What this attempt cost, and how much of its budget was left."""
        headroom = (
            self.max_tokens - self.output_tokens
            if isinstance(self.max_tokens, int) and isinstance(self.output_tokens, int)
            else None
        )
        return {
            "outcome": self.outcome,
            "max_tokens": self.max_tokens,
            "output_tokens": self.output_tokens,
            "stop_reason": self.stop_reason,
            "elapsed_seconds": self.elapsed_seconds,
            "headroom": headroom,
        }

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


# plan_8_3 stage 6: what each account fact is, in words a reader can act on. The provider's own
# sentence (which carries billing links and internal codes) stays in the log. Pending sign-off.
_ACCOUNT_WORDS = {
    "credit_exhausted": "its credit balance is used up.",
    "quota_exhausted": "its quota for this model is used up.",
    "model_not_entitled": "it is not entitled to the model bioAF is configured to use.",
    None: "the provider refused the request as a problem with the account itself.",
}


def _failure_reason(outcome: str, *, intent: str, model: str | None, fact: str | None = None) -> str:
    if outcome == OUTCOME_REFUSAL:
        return (
            f"The model {model or 'configured for this feature'} declined to answer while {intent}. "
            "An administrator may need to request an exception for this account."
        )
    if outcome == OUTCOME_UNREACHABLE:
        return f"bioAF could not reach the language model while {intent}."
    if outcome == OUTCOME_ACCOUNT:
        return (
            f"The language model account bioAF uses cannot run this request: {_ACCOUNT_WORDS.get(fact, _ACCOUNT_WORDS[None])} "
            f"This stopped bioAF while {intent}, and an administrator can put it right. The provider's own "
            "message is in bioAF's log."
        )
    if outcome == OUTCOME_UNPARSEABLE:
        return f"The model's answer while {intent} was not in the format bioAF asked for."
    if outcome == OUTCOME_TRUNCATED:
        return (
            f"The model's answer while {intent} was cut off at its token limit before it finished, "
            "so the part bioAF received could not be read as a complete answer."
        )
    if outcome == OUTCOME_TIMED_OUT:
        return (
            f"The model's answer while {intent} did not finish within bioAF's time limit, so bioAF "
            "received no complete answer."
        )
    if outcome == OUTCOME_SCHEMA_REJECTED:
        return f"The model's answer while {intent} did not hold what bioAF asked for."
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
    max_tokens: int | None = None,
    purpose: str | None = None,
) -> Decision:
    """Ask a model to decide something, and always return an account of what happened.

    ``intent`` is the step in the user's language ("binding the paper's claims"), not the function
    name: it is what the report shows and what every failure sentence is built from.

    ``allowed`` is optional and constrains VALUES. Absent means the model may answer anything, which
    is what lets the extractor and the generated-analysis arm use this function rather than fork it.

    ``max_tokens`` is the output budget (plan_8_1 section 1.1). It reaches the client only when given,
    so a caller that names none reaches its provider exactly as before.

    ``purpose`` is the decision's entry in plan_8_3 stage 6's audit. It supplies the budget when the
    caller names no explicit one, and the audit test walks the application for calls that name neither,
    so a caller added later cannot inherit an unreviewed default.
    """
    from app.services.validation_decision_budgets import budget_for

    allow = tuple(str(a) for a in allowed) if allowed is not None else None
    if max_tokens is None and purpose is not None:
        max_tokens = budget_for(purpose, model).max_tokens
    started = time.monotonic()

    def _elapsed() -> float:
        return round(time.monotonic() - started, 3)

    def _failed(
        outcome: str,
        detail: str,
        *,
        full_text: str = "",
        output_tokens: int | None = None,
        stop_reason: str | None = None,
        fact: str | None = None,
    ) -> Decision:
        logger.warning("llm decision failed (%s) while %s: %s", outcome, intent, detail)
        return Decision(
            outcome=outcome,
            reason=_failure_reason(outcome, intent=intent, model=model, fact=fact),
            model=model,
            intent=intent,
            allowed=allow,
            # Kept on the decision so the issue record carries the answer that could not be read.
            # A cause that is only in a truncated log line cannot be established later.
            text=full_text,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            max_tokens=max_tokens,
            elapsed_seconds=_elapsed(),
            purpose=purpose,
        )

    try:
        if max_tokens is None:
            text = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
        else:
            text = await client.submit(
                prompt=system, payload=payload, model=model, api_key=api_key, max_tokens=max_tokens
            )
    except ProviderError as exc:
        return _failed(
            _ERROR_CLASS_OUTCOMES.get(exc.error_class, OUTCOME_INTERNAL),
            f"{exc.error_class}: {exc}",
            # plan_8_1 section 1.2: a cut-off answer's text is kept for diagnosis. It is never parsed.
            full_text=getattr(exc, "text", None) or "",
            output_tokens=getattr(exc, "output_tokens", None),
            stop_reason=getattr(exc, "stop_reason", None),
            fact=getattr(exc, "account_fact", None),
        )
    except Exception as exc:  # noqa: BLE001 - asking a model for help cannot be allowed to fail a study
        logger.exception("llm decision raised while %s", intent)
        return _failed(OUTCOME_INTERNAL, str(exc))

    output_tokens = getattr(text, "output_tokens", None)
    stop_reason = getattr(text, "stop_reason", None)
    data = fenced_json(text)
    if data is None:
        # change_7.2 section 7: the whole answer, not 400 characters of it. No unparseable response
        # in either of the owner's runs could be attributed to a cause, because the evidence needed
        # to attribute it had already been thrown away by the time anyone read the log.
        return _failed(
            OUTCOME_UNPARSEABLE,
            (text or "")[:_LOG_ANSWER_CHARS],
            full_text=str(text or ""),
            output_tokens=output_tokens,
            stop_reason=stop_reason,
        )

    if "confidence" in data:
        data["confidence"] = confidence_of(data.get("confidence"))
    return Decision(
        outcome=OUTCOME_OK,
        data=data,
        model=model,
        intent=intent,
        text=str(text or ""),
        allowed=allow,
        reason=str(data.get("reason") or "").strip(),
        output_tokens=output_tokens,
        stop_reason=stop_reason,
        max_tokens=max_tokens,
        elapsed_seconds=_elapsed(),
        purpose=purpose,
    )


_FEEDBACK_HEADER = (
    "\n\nYour previous answer did not hold what bioAF asked for. Answer the SAME question again, in "
    "the format above, correcting exactly this:\n"
)
_UNPARSEABLE_FEEDBACK = "it was not a single fenced JSON object"


async def decide_with_recovery(
    *,
    intent: str,
    system: str,
    payload: str,
    client,
    model: str,
    api_key: str | None,
    purpose: str,
    allowed: Iterable[str] | None = None,
    max_tokens: int | None = None,
    validate=None,
    provider: str | None = None,
) -> Decision:
    """plan_8_3 stage 6: one decision, with at most ONE semantic recovery.

    A cut-off answer is asked again with a larger budget, within the model's documented maximum and
    what its measured rate can produce before the provider's deadline. An answer that arrived whole
    and did not satisfy ``validate`` is asked again once with exactly what was wrong. Anything else,
    a transport failure, a refusal, an internal error, is returned as it is: asking again would spend
    another call to fail the same way.

    ``validate(data) -> list[str]`` is the caller's own schema. Its problems are the feedback, and a
    second answer that still fails it ends the decision as ``schema_rejected`` rather than as a
    silently accepted partial result.
    """
    from app.services.llm_provider_clients import provider_of
    from app.services.validation_decision_budgets import larger_budget

    provider = provider or provider_of(client)
    attempts: list[dict] = []
    ask_payload, ask_budget, retried = payload, max_tokens, False

    while True:
        decision = await decide(
            intent=intent,
            system=system,
            payload=ask_payload,
            client=client,
            model=model,
            api_key=api_key,
            allowed=allowed,
            max_tokens=ask_budget,
            purpose=purpose,
        )
        problems = [str(p) for p in (validate(decision.data) if validate and decision.ok else []) if str(p).strip()]
        if decision.ok and problems:
            decision.outcome = OUTCOME_SCHEMA_REJECTED
            decision.reason = (
                f"{_failure_reason(OUTCOME_SCHEMA_REJECTED, intent=intent, model=model)} {'; '.join(problems)}"
            )
        attempts.append(
            {**decision.usage(), "attempt": len(attempts) + 1, **({"problems": problems} if problems else {})}
        )
        decision.attempts = attempts
        if decision.ok or retried or decision.outcome not in SEMANTIC_FAILURES:
            return decision

        spent = decision.max_tokens or ask_budget
        if decision.outcome == OUTCOME_TRUNCATED:
            bigger = larger_budget(spent, model=model, provider=provider, purpose=purpose) if spent else None
            if bigger is None:
                return decision
            ask_budget = bigger
        else:
            feedback = problems or [_UNPARSEABLE_FEEDBACK]
            ask_payload = payload + _FEEDBACK_HEADER + "\n".join(f"- {p}" for p in feedback)
        retried = True
        logger.info("llm decision retried once (%s) while %s under budget %s", decision.outcome, intent, ask_budget)
