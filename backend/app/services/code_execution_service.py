"""plan_7 step 17: what running the authors' code produced, and what it says about the paper.

Three questions, each a separate outcome, because a paper that fails at the first and a paper that
reaches the third and diverges are completely different findings:

1. Does the original scientist's code run at all?
2. Does it run correctly against the provided pre-processed data?
3. Does it produce the expected results?

**Failure is the RESULT, not an error to refuse on.** Steps 5, 6 and 8 refuse rather than guess,
because there proceeding would produce a confidently wrong SCIENTIFIC answer. Here the opposite
holds: running the authors' code against the authors' data and watching it fail IS the finding, and
a step that refused when the environment would not resolve would be discarding the very thing it was
built to discover. `dependency_unresolvable` on a five-year-old repo is a true and useful statement
about that paper's reproducibility, and today the feature reports it as `inconclusive` and says
nothing.

**Observations and causes are stored separately.** An earlier draft mapped each outcome straight
onto a cause. Both inferences were unsound, and one contradicted this plan's founding evidence:
study 26 diverged nearly two-fold and its own reasoning concluded a peak-caller difference ON OUR
SIDE plausibly explained the gap, so "the paper cannot be indicted". A mapping table would have
overridden that.

- **The observation** is deterministic, from the execution itself: the outcome token, the exit
  status, the transcript, the identifiers that failed to match, the paper's number and ours. **No
  cause.**
- **The assessment** is what could explain it: model-made, through the plan_6 seam, in its own key,
  and its candidate set ALWAYS includes bioAF's own input selection, column mapping, chosen entry
  point and arguments, and the execution environment.

"We ran their code and got a different number, and we do not know why" is a publishable, honest
result. "Their logic is wrong" from the same evidence is not.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re

from app.services.llm_decision import decide

logger = logging.getLogger("bioaf.code_execution")

# ---- the outcome vocabulary -----------------------------------------------------------------
#
# In the spirit of `VALIDATION_STUDY_CLASSIFICATIONS`, so a verdict names the defect instead of
# saying "inconclusive". Every one of these is a publishable observation about a paper.

CODE_ABSENT = "code_absent"
CODE_UNREACHABLE = "code_unreachable"
DEPENDENCY_UNRESOLVABLE = "dependency_unresolvable"
CODE_INCOMPLETE = "code_incomplete"
CODE_ERROR = "code_error"
DATA_MISMATCH = "data_mismatch"
GENERATION_FAILED = "generation_failed"
RAN_NO_OUTPUT = "ran_no_output"
RAN_OUTPUT_UNCOMPARABLE = "ran_output_uncomparable"
RAN_OUTPUT_DIVERGES = "ran_output_diverges"
RAN_OUTPUT_AGREES = "ran_output_agrees"

OUTCOMES = (
    CODE_ABSENT,
    CODE_UNREACHABLE,
    DEPENDENCY_UNRESOLVABLE,
    CODE_INCOMPLETE,
    CODE_ERROR,
    DATA_MISMATCH,
    GENERATION_FAILED,
    RAN_NO_OUTPUT,
    RAN_OUTPUT_UNCOMPARABLE,
    RAN_OUTPUT_DIVERGES,
    RAN_OUTPUT_AGREES,
)

# Carried BESIDE an outcome, never instead of one. A qualifier never upgrades or downgrades an
# outcome by itself; it changes what the report is allowed to claim from it.
QUALIFIER_METHODS_INADEQUATE = "methods_inadequate"
QUALIFIER_GENERATED_FROM_PROSE = "generated_from_prose"
QUALIFIER_ASSUMPTIONS_RECORDED = "assumptions_recorded"
QUALIFIER_ROUTE_DEPOSIT = "route_deposit"

METHOD_AUTHORS_CODE = "authors_code"
METHOD_LLM_FROM_METHODS = "llm_from_methods"

ENTRY_POINT_INTENT = "choosing which script in the authors' repository starts their analysis"

# What a transcript says, in the order that keeps a specific cause ahead of a general one. Anchored
# on the phrases the tools themselves emit rather than on words that could appear anywhere.
_DEPENDENCY_SIGNATURES = (
    "could not find a version that satisfies",
    "no matching distribution found",
    "unable to locate package",
    "resolvepackagenotfound",
    "package installation failed",
    "installation of package",
    "had non-zero exit status",
    "conflictingdependencyerror",
    "error: dependencies",
)
_INCOMPLETE_SIGNATURES = (
    "there is no package called",
    "no module named",
    "cannot open file",
    "no such file or directory",
    "file not found",
    "cannot open the connection",
)


def classify_transcript(transcript: str, *, exit_code: int) -> str | None:
    """What the transcript establishes, or None when nothing went wrong.

    Deterministic and evidence-first: these are the outcomes a transcript can ESTABLISH, not the
    causes it might suggest. Where the evidence does not reach a specific cause the answer is
    ``code_error``, which says "it started and then failed on its own" and nothing more.

    A clean exit code is not proof of success. A headless notebook that raised still exits its pod
    cleanly and reports `completed`; study 13 is the study that taught this feature that lesson, so
    the transcript is read either way.
    """
    text = (transcript or "").lower()

    if any(sig in text for sig in _DEPENDENCY_SIGNATURES):
        return DEPENDENCY_UNRESOLVABLE
    if any(sig in text for sig in _INCOMPLETE_SIGNATURES):
        return CODE_INCOMPLETE

    failed = exit_code != 0 or "traceback (most recent call last)" in text or "\nerror in " in f"\n{text}"
    return CODE_ERROR if failed else None


def build_observation(
    *,
    outcome: str,
    exit_code: int | None,
    transcript_uri: str | None,
    transcript_tail: str,
    qualifiers: list[str] | None = None,
    paper_value: float | int | None = None,
    our_value: float | int | None = None,
    metric: str | None = None,
    unmatched: list | None = None,
) -> dict:
    """The deterministic record of what happened. **No cause, and no explanation.**

    Kept apart from the assessment so the two are never mistaken for each other, in the code or in
    the report. Where a run diverged, BOTH numbers are here: the report always shows ours beside the
    paper's, and lets the reader judge.
    """
    observation = {
        "outcome": outcome,
        "qualifiers": list(qualifiers or []),
        "exit_code": exit_code,
        "transcript_uri": transcript_uri,
        # The last of the transcript, so the outcome has its argument attached without putting a
        # megabyte of install log on the evidence bundle. The whole log lives at `transcript_uri`.
        "transcript_tail": (transcript_tail or "")[-4000:],
    }
    if metric is not None:
        observation["metric"] = metric
    if paper_value is not None:
        observation["paper_value"] = paper_value
    if our_value is not None:
        observation["our_value"] = our_value
    if unmatched:
        observation["unmatched"] = unmatched
    return observation


# ---- the entry point ---------------------------------------------------------------------------

_ENTRY_POINT_SYSTEM = (
    "You are choosing which file in a published analysis repository starts the analysis. You are "
    "given the repository's file listing and its README, and nothing else: you cannot see the "
    "contents of any script and you must not pretend to.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"entry_point": "exact path from the listing", "arguments": "command-line arguments, or empty", '
    '"reason": "one sentence", "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- Use a path EXACTLY as listed. Do not invent one; a path that is not in the listing will be "
    "rejected and nothing will run.\n"
    "- Choose the script that performs the paper's ANALYSIS, not one that only downloads data, "
    "renders a figure from an already-computed table, or installs dependencies.\n"
    "- Where the README names a command, follow it.\n"
    "- If nothing in the listing looks like an analysis entry point, set entry_point to null and "
    "say why. That is a real answer."
)


async def choose_entry_point(*, files: list[dict], readme: str, client, model: str, api_key: str | None) -> dict:
    """Which script to run, and with what arguments. A stated choice, a reason, a confidence.

    Rarely stated in a paper, so it goes through the same decide-and-record seam as steps 2 and 7,
    and **deterministic code checks the file exists before anything runs**: a model naming a script
    that is not there would launch a pod that fails for a reason nobody could see.
    """
    paths = [str(f.get("path")) for f in files or [] if f.get("path")]
    empty = {
        "entry_point": None,
        "arguments": "",
        "reason": "",
        "confidence": 0.0,
        "decided_by": "model",
        "model": model,
    }
    if not paths:
        return {**empty, "reason": "the fetched repository holds no files"}

    payload = "Repository files:\n" + "\n".join(f"  {p}" for p in paths)
    if (readme or "").strip():
        payload += "\n\nREADME:\n" + readme[:8000]

    decision = await decide(
        intent=ENTRY_POINT_INTENT,
        system=_ENTRY_POINT_SYSTEM,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        allowed=paths,
    )
    if not decision.ok:
        return {**empty, "reason": decision.reason}

    chosen = decision.choice("entry_point")
    reason = decision.reason
    if chosen is None and decision.data.get("entry_point"):
        reason = (
            f"the model named {decision.data['entry_point']}, which the fetched repository does not "
            f"contain{': ' + reason if reason else ''}"
        )
    return {
        "entry_point": chosen,
        "arguments": str(decision.data.get("arguments") or "").strip(),
        "reason": reason,
        "confidence": decision.confidence(),
        "decided_by": "model",
        "model": model,
    }


# ---- the output adapters -----------------------------------------------------------------------
#
# Three supported shapes, tried in order. An earlier draft required every execution to normalize to
# id / lfc / padj and called anything else `ran_no_output`. That is narrower than the goal: a script
# that computes "we identified 1,412 peaks" ran successfully and answered the paper's claim, and
# labelling it "no output" would be a false statement about the paper.

_TABLE_SUFFIXES = (".csv", ".tsv", ".txt")
_ID_COLUMNS = ("gene", "gene_id", "id", "name", "symbol", "feature", "peak", "region")
_LFC_COLUMNS = ("log2foldchange", "log2fc", "logfc", "lfc", "fold_change")
_PADJ_COLUMNS = ("padj", "p_adj", "fdr", "qvalue", "q_value", "adj.p.val", "adj_pval")


def _looks_differential(text: str) -> bool:
    """Whether a table resolves to id / lfc / padj, which is today's comparison path unchanged."""
    header = (text or "").splitlines()[:1]
    if not header:
        return False
    delimiter = "\t" if "\t" in header[0] else ","
    cols = [c.strip().strip('"').lower() for c in next(csv.reader(io.StringIO(header[0]), delimiter=delimiter), [])]
    return (
        any(c in _ID_COLUMNS for c in cols)
        and any(c in _LFC_COLUMNS for c in cols)
        and any(c in _PADJ_COLUMNS for c in cols)
    )


def _scalars(text: str, path: str) -> dict[str, float]:
    """Named numbers in a machine-readable output: a JSON object, or a two-column key/value table."""
    stripped = (text or "").strip()
    if not stripped:
        return {}
    if path.lower().endswith(".json") or stripped[:1] in "{[":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

    out: dict[str, float] = {}
    delimiter = "\t" if "\t" in stripped.splitlines()[0] else ","
    for row in csv.reader(io.StringIO(stripped), delimiter=delimiter):
        if len(row) != 2:
            continue
        key, raw = row[0].strip(), row[1].strip()
        if not key or not re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", raw):
            continue
        out[key] = float(raw)
    return out


def adapt_outputs(*, outputs: list[dict], claims: list[dict]) -> dict:
    """Land what the run wrote where bioAF can compare it, or say honestly that we cannot.

    Three distinct outcomes, not one. "Wrote nothing" is ``ran_no_output``. "Wrote something we
    cannot compare" is ``ran_output_uncomparable`` and is a limitation of BIOAF, stated as such.
    "Wrote something we compared" leaves the outcome to the comparison layer.

    **Matching is explicit and refusable.** A numeric output is tied to a paper claim only when it
    names the same metric; where the tie cannot be made the value is retained and reported as
    unmatched. An unmatched output can support neither agreement nor divergence, and inventing the
    match would be the same defect as scoring a claim against a metric bioAF does not compute.
    """
    files = [o for o in outputs or [] if (o.get("text") or "").strip()]
    if not (outputs or []):
        return {
            "kind": None,
            "outcome": RAN_NO_OUTPUT,
            "reason": "the run completed and wrote nothing at all",
            "unmatched": [],
        }

    for out in files:
        if str(out.get("path", "")).lower().endswith(_TABLE_SUFFIXES) and _looks_differential(out["text"]):
            return {
                "kind": "differential_table",
                # Left to the concordance service, which already scores agreement against the
                # paper's own set. Do not build a second comparator.
                "outcome": None,
                "path": out["path"],
                "table_text": out["text"],
                "reason": f"{out['path']} resolves to a differential table",
                "unmatched": [],
            }

    wanted = {str(c.get("metric_key") or "").strip() for c in claims or [] if c.get("metric_key")}
    computed: dict[str, float] = {}
    unmatched: list[dict] = []
    for out in files:
        for key, value in _scalars(out["text"], str(out.get("path", ""))).items():
            if key in wanted:
                computed[key] = value
            else:
                unmatched.append({"path": out["path"], "name": key, "value": value})

    if computed:
        return {
            "kind": "numeric_claim",
            # The existing claim/tolerance machinery in `validation_classifier_service` decides
            # agree vs diverge from here.
            "outcome": None,
            "computed_metrics": computed,
            "reason": f"matched {len(computed)} of the paper's claims to a number the run wrote",
            "unmatched": unmatched,
        }

    return {
        "kind": "unsupported",
        "outcome": RAN_OUTPUT_UNCOMPARABLE,
        "reason": (
            "the run completed and wrote output, and bioAF's comparison layer does not support its "
            "shape. This is a limitation of bioAF, not a defect of the paper: interpreting figures "
            "and supporting every analysis format are explicitly out of scope."
        ),
        "unmatched": unmatched or [{"path": o.get("path"), "name": None, "value": None} for o in outputs],
    }
