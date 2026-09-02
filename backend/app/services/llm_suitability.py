"""plan_6 step 7: whether the model an org chose can do literature validation at all.

Curated judgment, deliberately. There is no eval harness behind these verdicts: they are read off
context-window sizes and structured-output behaviour, and they are a RELEASE ACTIVITY. Every time a
model family ships, someone has to decide whether this table still tells the truth. `plan_7`'s eval
harness is what replaces judgment with measurement, and until it exists this docstring is the honest
statement of what a verdict here is worth.

**An unknown model is `unproven`, never `unlikely`.** The table fails open on missing information. A
model released the week after this file was last edited must not be labelled bad because nobody has
got to it yet, and the cost of that rule is that a genuinely unsuitable new model goes unwarned
until someone adds it. That is the right way round: a wrong warning teaches users to ignore
warnings, and then the real ones stop working too.

The banner states the REASON, not the verdict. "This model's context window is smaller than a
typical full paper" is something a user can check against their own papers. "Unsuitable" is not, and
it does not tell them what would change the answer.

Nothing here blocks a save. The user proceeds at their discretion.
"""

from __future__ import annotations

KNOWN_GOOD = "known_good"
UNPROVEN = "unproven"
UNLIKELY = "unlikely"

_PROVISIONAL = (
    "This assessment is provisional: it is bioAF's judgment from the model's published context "
    "window and structured-output behaviour, not a measurement of this feature running on it."
)

# Keyed by (provider, model-name prefix). The longest matching prefix wins, so a family can be marked
# good with one member excepted. Only known_good and unlikely belong here; unproven is what absence
# already means, and writing it down would suggest the model was assessed and found wanting.
LLM_SUITABILITY: dict[tuple[str, str], dict[str, str]] = {
    ("anthropic", "claude-opus"): {
        "verdict": KNOWN_GOOD,
        "reason": "holds a full paper in context and returns well-formed JSON reliably.",
    },
    ("anthropic", "claude-sonnet"): {
        "verdict": KNOWN_GOOD,
        "reason": (
            "holds a full paper in context and returns well-formed JSON reliably. This is the model "
            "the feature's binding behaviour was measured on."
        ),
    },
    ("anthropic", "claude-haiku"): {
        "verdict": KNOWN_GOOD,
        "reason": "holds a full paper in context; expect more declined claims than a larger model.",
    },
    ("openai", "gpt-5"): {
        "verdict": KNOWN_GOOD,
        "reason": "holds a full paper in context and returns well-formed JSON reliably.",
    },
    ("google", "gemini-2.5-pro"): {
        "verdict": KNOWN_GOOD,
        "reason": (
            "holds a full paper in context and returns well-formed JSON. Measured binding a ChIP-seq "
            "paper's headline peak count correctly on 2026-09-02."
        ),
    },
    ("google", "gemini-2.5-flash"): {
        "verdict": KNOWN_GOOD,
        "reason": "holds a full paper in context; expect more declined claims than a larger model.",
    },
    # The one family the product ships an integration for that cannot do this job. A 9B self-hosted
    # model has neither the context for a full paper nor dependable fenced-JSON output, and a parse
    # failure here is recorded as a blocker on the study rather than as an error the user can act on.
    ("gemma", "gemma"): {
        "verdict": UNLIKELY,
        "reason": (
            "this model's context window is smaller than a typical full paper, so extraction will "
            "fail or read only part of the methods on most papers, and its structured output is not "
            "dependable enough for the JSON this feature requires."
        ),
    },
}


def suitability_for(provider: str | None, model: str | None) -> dict:
    """The suitability verdict for one (provider, model), never raising and never guessing badly.

    Returns ``verdict``, a ``reason`` written for a user rather than an engineer, whether to ``warn``,
    the ``note`` that says the assessment is provisional, and ``blocks_save`` which is always False.
    """
    name = (model or "").strip().lower()
    key = (provider or "").strip().lower()

    best: tuple[str, dict] | None = None
    if name and key:
        for (entry_provider, prefix), entry in LLM_SUITABILITY.items():
            if entry_provider != key or not name.startswith(prefix):
                continue
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, entry)

    if best is None:
        return {
            "verdict": UNPROVEN,
            "reason": (
                "bioAF has not assessed this model for literature validation. It may work well; "
                "nobody has checked. Read the first study's decisions closely."
            ),
            "warn": False,
            "note": _PROVISIONAL,
            "blocks_save": False,
        }

    entry = best[1]
    return {
        "verdict": entry["verdict"],
        "reason": entry["reason"],
        "warn": entry["verdict"] == UNLIKELY,
        "note": _PROVISIONAL,
        "blocks_save": False,
    }
