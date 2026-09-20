"""plan_8_4 sections 6.1 and 6.2: the evidence bioAF already holds, mapped to rubric v3's obligations.

One rule decides every mapping, and it is deliberately unkind to bioAF:

- **verified** only where held evidence ESTABLISHES the obligation. A repository that exists is not
  code that parses; a retrieval that succeeded is not a syntax check; a model saying a methods section
  is "detailed enough" is one holistic opinion and answers no criterion this rubric declares.
- **failed** only where held evidence CONTRADICTS it, with the cause and its impact recorded. A
  measured species disagreement is a failure. A field bioAF's own extraction did not fill is not:
  that is bioAF's limitation, and section 3.4 says a limitation of bioAF produces undetermined points.
- **undetermined** everywhere else, including every obligation whose check is not implemented yet.
  Those are declared in ``CAPABILITY_LIMITS`` and listed on the report, so a grey obligation reads as
  a capability bioAF has not delivered rather than as a completed check that found nothing.

Pure: no database, no model, no I/O. Everything here is read from evidence the study already holds.
"""

from __future__ import annotations

from app.services.validation_documentary_review import JUDGED_LEAVES
from app.services.validation_rubric_v3 import FAILED, UNDETERMINED, VERIFIED

MEASUREMENT = "measurement"
MODEL_ASSISTED = "model_assisted"
HUMAN_ASSISTED = "human_assisted"

# An outcome that settled a claim against the authors' own published results, and one that did not.
_AGREES = "agree"
_DISAGREES = "disagree"


def _finding(outcome: str, rationale: str, *, scope: str, method: str = MEASUREMENT, **extra) -> dict:
    return {"outcome": outcome, "rationale": rationale, "scope": scope, "method": method, **extra}


def _unjudged(leaf: str) -> dict:
    """plan_8_5 section 3.6: an obligation whose assessor exists and had nothing to work with.

    It is not a capability limit: bioAF implements the check. What it lacks for THIS study is the
    paper's own passages or a configured model, and the documentary review records which.
    """
    return _finding(
        UNDETERMINED,
        "bioAF holds no passages of this paper for its assessor to judge this obligation on",
        scope="the paper's own text",
        next_action="read the paper again so its methods are held, and run the documentary review",
    )


def _open(rationale: str, *, scope: str, next_action: str, **extra) -> dict:
    """An obligation nothing established. It always says what would establish it, or that nothing can."""
    return _finding(UNDETERMINED, rationale, scope=scope, next_action=next_action, **extra)


# ---- what has no implemented check yet ------------------------------------------------------------
#
# Section 10: every unsupported obligation is enumerated. This is the honest inventory of what rubric
# v3 declares and bioAF cannot yet assess, and it is what the report lists as capability limits.

_CODE_LIMIT = (
    "bioAF holds no implemented check for this obligation. Establishing it means parsing, resolving "
    "and loading the paper's actual source in its declared environment, which runs untrusted code and "
    "belongs behind the existing isolated execution path and its approval."
)

CAPABILITY_LIMITS: dict[str, dict] = {
    # plan_8_4 milestone B: the static code obligations are implemented (`validation_code_checks`).
    # What remains a standing limit is the two that cannot be established by reading source at all.
    # plan_8_5 gate 2: the sample obligations (S2, S4.B, S5.A) are implemented and are no longer here.
    "C1.B": {"reason": _CODE_LIMIT},
    "C2.B": {"reason": _CODE_LIMIT},
    "R2": {
        "reason": "an independent result assessment requires an approved analysis run over acquired "
        "inputs. Nothing is assessed until one has run."
    },
    "R3": {"reason": "end-to-end execution requires an approved workflow run. Nothing is assessed until one has run."},
}


def assess_evidence(
    *,
    plan: dict | None,
    evidence: dict | None,
    claims: list[dict] | None = None,
    inventory: dict | None = None,
) -> dict:
    """Every rubric v3 leaf this build can settle from the study's held evidence, keyed by leaf id.

    A leaf absent from the result is undetermined, which is what ``score`` reads it as. Present-and-
    undetermined is used where bioAF looked and could not establish the obligation, so the difference
    between "not implemented" and "checked and open" stays visible in the detail.
    """
    plan = plan or {}
    evidence = evidence or {}
    experiments = [e for e in plan.get("reported_experiments") or [] if isinstance(e, dict)]
    contrasts = [c for c in (plan.get("differential_design") or {}).get("contrasts") or [] if isinstance(c, dict)]
    assessed: dict[str, dict] = {}
    assessed.update(_species(experiments, plan, evidence))
    assessed.update(_material(evidence))
    assessed.update(_groups(contrasts, evidence))
    assessed.update(_accounting(experiments, plan, evidence))
    assessed.update(_replication(evidence))
    assessed.update(_units(evidence))
    assessed.update(_references(experiments))
    assessed.update(_decision_criteria(claims or [], contrasts, plan.get("differential_design")))
    assessed.update(_author_results(claims or [], inventory))
    assessed.update(_code(evidence))
    assessed = _with_judgments(assessed, evidence)
    for leaf_id in JUDGED_LEAVES:
        assessed.setdefault(leaf_id, _unjudged(leaf_id))
    for leaf_id, limit in CAPABILITY_LIMITS.items():
        assessed.setdefault(
            leaf_id,
            _finding(UNDETERMINED, limit["reason"], scope="not assessed", method=MEASUREMENT, capability_limit=True),
        )
    return assessed


def _with_judgments(assessed: dict, evidence: dict) -> dict:
    """The accepted documentary judgments, over the obligations no measurement settled.

    plan_8_5 section 3.6. A measurement outranks a judgment: where bioAF compared something and got
    an answer, that answer stands and the model's opinion about the same obligation does not
    overwrite it. Where the measurement could not conclude, an evidence-backed judgment is what the
    obligation has, and it carries its own citations and the assessor that made it.
    """
    held = evidence.get("rubric_judgments")
    judgments = (held or {}).get("judgments") if isinstance(held, dict) else None
    if not isinstance(judgments, dict):
        return assessed
    merged = dict(assessed)
    for leaf_id, judgment in judgments.items():
        if not isinstance(judgment, dict) or judgment.get("outcome") not in (VERIFIED, FAILED, UNDETERMINED):
            continue
        settled = merged.get(leaf_id) or {}
        if settled.get("outcome") in (VERIFIED, FAILED):
            continue
        if judgment["outcome"] == UNDETERMINED and settled and not settled.get("capability_limit"):
            # Two open answers about one obligation: keep the one that says what would settle it.
            if settled.get("next_action") and not judgment.get("next_action"):
                continue
        merged[leaf_id] = judgment
    return merged


def _code(evidence: dict) -> dict:
    """plan_8_4 milestone B: the code section, from the source this study actually holds.

    ``evidence["code_inspection"]`` is what an inspection stage recorded: the source text it extracted,
    the manifests beside it, any evidence-backed fitness review, and the result of an approved isolated
    run. Nothing else in the study's evidence says anything about the code: a repository that exists, a
    file that was retrieved and a role that says "code" are not source text, and the checks say so by
    leaving every obligation grey.
    """
    from app.services.validation_code_checks import assess_code

    inspection = evidence.get("code_inspection") or {}
    if not isinstance(inspection, dict):
        return {}
    return assess_code(
        sources=inspection.get("sources"),
        manifests=inspection.get("manifests"),
        defects=inspection.get("reviews"),
        execution=inspection.get("execution"),
    )


def _species(experiments: list[dict], plan: dict, evidence: dict) -> dict:
    stated = [str(e.get("organism") or "").strip() for e in experiments]
    sheet = str((plan.get("sample_sheet") or {}).get("organism") or "").strip()
    named = [o for o in stated if o] or ([sheet] if sheet else [])
    scope = f"{len(experiments)} reported {'experiment' if len(experiments) == 1 else 'experiments'}"
    # Every relevant experiment states one, or, where the plan records no experiments at all, the
    # study's sample sheet does. One experiment left blank is not "the paper states the organism".
    every_experiment = bool(stated) and all(stated)
    if every_experiment or (not stated and bool(sheet)):
        found = _finding(
            VERIFIED,
            f"the paper states the organism for every relevant experiment: {', '.join(sorted(set(named)))}",
            scope=scope,
        )
    else:
        found = _open(
            "bioAF's read of the paper recorded no organism for every relevant experiment",
            scope=scope,
            next_action="read the paper's samples section again, or record the organism at the gate",
        )
    return {"S1.A": found, "S1.B": _species_agreement(named, evidence)}


def _norm_organism(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def deposit_samples(evidence: dict) -> list[tuple[dict, dict]]:
    """(deposit, sample) for every sample record this study holds, keeping each sample's deposit."""
    held = evidence.get("sample_records") if isinstance(evidence.get("sample_records"), dict) else {}
    pairs = []
    for deposit in held.get("deposits") or []:
        if not isinstance(deposit, dict):
            continue
        for sample in deposit.get("samples") or []:
            if isinstance(sample, dict):
                pairs.append((deposit, sample))
    return pairs


def record_limitations(evidence: dict) -> list[str]:
    """Why bioAF holds no sample records for a deposit, in the words the retrieval recorded."""
    held = evidence.get("sample_records") if isinstance(evidence.get("sample_records"), dict) else {}
    return [str(lim.get("reason") or "") for lim in held.get("limitations") or [] if isinstance(lim, dict)]


def _records_scope(pairs: list[tuple[dict, dict]]) -> str:
    accessions = sorted({str(deposit.get("accession") or "") for deposit, _ in pairs} - {""})
    samples = len(pairs)
    return f"{samples} sample {'record' if samples == 1 else 'records'} from {', '.join(accessions) or 'no deposit'}"


def _species_agreement(named: list[str], evidence: dict) -> dict:
    """S1.B: the organisms the paper states, against the ones the deposit states for its samples.

    plan_8_5 section 3.4. The deposit's own per-sample declaration is the authority, and a deposit
    holding two organisms (a xenograft, a spike-in) agrees with a paper that names the one it
    analysed. What bioAF could not retrieve is named as bioAF's limitation, never as a deposit that
    declares nothing.
    """
    pairs = deposit_samples(evidence)
    if pairs:
        declared = {_norm_organism(sample.get("organism")): str(sample.get("organism") or "") for _, sample in pairs}
        declared.pop("", None)
        scope = _records_scope(pairs)
        if not declared:
            return _open(
                "every sample record bioAF read states no organism",
                scope=scope,
                next_action="read the deposit's sample metadata again, or record the organism at the gate",
            )
        if not named:
            return _open(
                "bioAF's read of the paper recorded no organism to compare with the deposit's records",
                scope=scope,
                next_action="record the organism the paper states, with its quote",
            )
        missing = [organism for organism in named if _norm_organism(organism) not in declared]
        if missing:
            disagreeing = sorted({sample.get("accession") or "" for _, sample in pairs} - {""})[:4]
            return _finding(
                FAILED,
                f"the paper states {', '.join(sorted(set(missing)))} and the deposit's sample records state "
                f"{', '.join(sorted(declared.values()))}"
                + (f" ({', '.join(disagreeing)})" if disagreeing else ""),
                scope=scope,
                impact=(
                    "an analysis against the paper's stated organism would align the wrong species and answer "
                    "confidently about it"
                ),
            )
        return _finding(
            VERIFIED,
            f"the deposit's own sample records state {', '.join(sorted(set(named)))} for the samples the paper uses",
            scope=scope,
        )
    limitations = record_limitations(evidence)
    if limitations:
        return _open(
            "; ".join(sorted(set(limitations))),
            scope="no sample record bioAF could read",
            next_action="acquire the deposit's sample metadata",
        )
    # Nothing retrieved this run: an older check that DID compare the deposit's declaration still
    # establishes what it measured, and one that compared nothing still establishes nothing.
    check = (evidence.get("precompute_checks") or {}).get("species_matches") or {}
    verdict = check.get("verdict")
    detail = str(check.get("detail") or "").strip()
    if verdict == "ok":
        return _finding(
            VERIFIED,
            detail or "the deposited sample records state the organisms the paper states",
            scope="the sample records bioAF holds",
        )
    if verdict == "mismatch":
        return _finding(
            FAILED,
            detail or "the deposited sample records contradict the organism the paper states",
            scope="the sample records bioAF holds",
            impact="an analysis against the paper's stated organism would answer confidently about the wrong species",
        )
    return _open(
        "bioAF holds no sample records stating an organism to compare",
        scope="no sample record bioAF could read",
        next_action="acquire the deposit's sample metadata",
    )


def _groups(contrasts: list[dict], evidence: dict) -> dict:
    scope = f"{len(contrasts)} {'contrast' if len(contrasts) == 1 else 'contrasts'}"
    missing = [
        c.get("name") or "an unnamed contrast"
        for c in contrasts
        if not (str(c.get("test_condition") or "").strip() and str(c.get("reference_condition") or "").strip())
    ]
    if contrasts and not missing:
        defined = _finding(
            VERIFIED,
            "every comparison the paper's claims rest on names its test and its reference arm",
            scope=scope,
        )
    else:
        defined = _open(
            (
                f"{', '.join(missing)} names no test or reference arm"
                if missing
                else "the paper's claims rest on no defined comparison"
            ),
            scope=scope,
            next_action="record the arms at the gate, or read the paper's design again",
        )
    return {"S3.A": defined, "S3.B": _arm_support(contrasts, evidence)}


def _conditions(contrast: dict) -> list[str]:
    return [
        str(contrast.get(key) or "").strip()
        for key in ("test_condition", "reference_condition")
        if str(contrast.get(key) or "").strip()
    ]


def _arm_support(contrasts: list[dict], evidence: dict) -> dict:
    """S3.B: whether independent records support assigning samples to the arms the paper defines.

    plan_8_5 section 3.4: this is a documentary check, and it does not wait for an analysis input to
    be selected. The deposit's own per-sample characteristics answer it where they name the paper's
    arms; an accepted column mapping answers it too, and neither is a prerequisite for the other.
    """
    pairs = deposit_samples(evidence)
    wanted = {_norm_organism(c) for contrast in contrasts for c in _conditions(contrast)} - {""}
    if pairs and wanted:
        described = {
            _norm_organism(value)
            for _d, sample in pairs
            for value in list((sample.get("characteristics") or {}).values())
            + list(sample.get("unkeyed") or [])
            + [sample.get("title"), sample.get("source_name")]
            if str(value or "").strip()
        }
        matched = sorted(
            arm for arm in wanted if any(arm in value or value in arm for value in described if value)
        )
        if matched:
            return _finding(
                VERIFIED,
                f"the deposit's own sample records name the arms the paper compares ({', '.join(matched)})",
                scope=_records_scope(pairs),
            )
    validation = ((evidence.get("input_choice") or {}).get("mapping_validation")) or {}
    rows = [r for r in validation.get("mapping") or [] if isinstance(r, dict)]
    if validation.get("status") == "accepted" and rows:
        return _finding(
            VERIFIED,
            f"every one of the {len(rows)} chosen columns was placed in an arm the sample records support",
            scope=f"{len(rows)} columns of the chosen input",
            method=HUMAN_ASSISTED if validation.get("assistance") else MEASUREMENT,
        )
    if pairs and wanted:
        return _open(
            "the deposit's sample records name none of the arms the paper compares",
            scope=_records_scope(pairs),
            next_action="check which deposited samples belong to each arm, and record what says so",
        )
    if validation:
        return _open(
            "; ".join(validation.get("reasons") or []) or "the sample mapping is not accepted",
            scope="the chosen input's columns",
            next_action="resolve the mapping's refusals at the gate",
        )
    return _open(
        "; ".join(sorted(set(record_limitations(evidence))))
        or "bioAF holds no sample records placing this paper's samples in its arms",
        scope=_records_scope(pairs) if pairs else "no sample record bioAF could read",
        next_action="acquire the deposit's sample metadata, or an analysis input and map its columns",
    )


def _accounting(experiments: list[dict], plan: dict, evidence: dict) -> dict:
    """S4: the counts the paper states, and the records reconciled to them."""
    count = (plan.get("sample_sheet") or {}).get("sample_count")
    per_experiment = [e.get("sample_count") for e in experiments]
    stated = [c for c in [*per_experiment, count] if isinstance(c, int) and c > 0]
    if stated:
        states = _finding(
            VERIFIED,
            f"the paper states how many samples the study used ({stated[0]})",
            scope="the study's stated sample counts",
        )
    else:
        states = _open(
            "bioAF's read of the paper recorded no sample count for the relevant experiments",
            scope="the study's stated sample counts",
            next_action="read the paper's samples section again",
        )
    return {"S4.A": states, "S4.B": _reconciled_counts(experiments, plan, evidence, stated)}


def _reconciled_counts(experiments: list[dict], plan: dict, evidence: dict, stated: list[int]) -> dict:
    """S4.B: the held sample records against the count the paper states for the same set.

    plan_8_5 section 3.4 and plan_8_4 section 3.2: a whole-series count is never substituted for an
    experiment's count. Where the paper states a count per experiment and the deposit holds one
    series covering several of them, the two are not describing the same set, and comparing them
    would manufacture a contradiction out of a scope difference.
    """
    pairs = deposit_samples(evidence)
    if not pairs:
        return _open(
            "; ".join(sorted(set(record_limitations(evidence))))
            or "bioAF holds no sample records to reconcile the paper's counts against",
            scope="no sample record bioAF could read",
            next_action="acquire the deposit's sample metadata",
        )
    scope = _records_scope(pairs)
    if not stated:
        return _open(
            "bioAF's read of the paper recorded no sample count to reconcile these records against",
            scope=scope,
            next_action="read the paper's samples section again",
        )
    per_experiment = [c for c in (e.get("sample_count") for e in experiments) if isinstance(c, int) and c > 0]
    deposits = {str(deposit.get("accession") or "") for deposit, _s in pairs}
    if len(per_experiment) > 1 and len(deposits) < len(per_experiment):
        return _open(
            f"the paper states a count for each of {len(per_experiment)} experiments and the records bioAF "
            f"holds are {len(deposits)} deposit(s); a whole-series count is not an experiment's count",
            scope=scope,
            next_action="establish which deposited samples belong to each reported experiment",
        )
    held = len(pairs)
    expected = stated[0]
    if held == expected:
        return _finding(
            VERIFIED,
            f"the {held} sample records bioAF holds reconcile to the {expected} the paper states",
            scope=scope,
        )
    return _finding(
        FAILED,
        f"the paper states {expected} sample(s) and the deposit holds {held} record(s) for the same set",
        scope=scope,
        impact="one of the two is not describing the set that was analysed, so a count taken from either is unsafe",
        evidence={"stated": expected, "held": held, "deposits": sorted(deposits)},
    )


def _units(evidence: dict) -> dict:
    validation = ((evidence.get("input_choice") or {}).get("mapping_validation")) or {}
    unresolved = [str(c) for c in validation.get("units_unresolved") or []]
    if validation and not unresolved and validation.get("status") == "accepted":
        return {
            "S5.B": _finding(
                VERIFIED,
                "the biological unit each chosen column came from is established from the sample records",
                scope="the chosen input's columns",
                method=HUMAN_ASSISTED if validation.get("assistance") else MEASUREMENT,
            )
        }
    if unresolved:
        return {
            "S5.B": _open(
                f"nothing the study cites states which biological unit {', '.join(unresolved)} came from",
                scope="the chosen input's columns",
                next_action=f"record the biological unit of {', '.join(unresolved)}, with the evidence for it",
            )
        }
    return {
        "S5.B": _open(
            "no analysis input was chosen, so no column's biological unit was established",
            scope="no chosen input",
            next_action="acquire an analysis input and map its columns",
        )
    }


def paper_statement(evidence: dict, name: str) -> dict | None:
    """One fact about the paper that bioAF recorded, with how it was established, or None.

    plan_8_5 sections 3.4 and 3.6: a documentary obligation about what the PAPER says needs the
    paper's own statement, and bioAF's extraction fills no field for the material a sample came from
    or the replication a design used. They are recorded here by the documentary stage, with the
    passage each rests on, and an obligation that has no statement stays untested rather than being
    guessed at from a sample's name.
    """
    statements = evidence.get("paper_statements")
    statement = (statements or {}).get(name) if isinstance(statements, dict) else None
    if not isinstance(statement, dict) or not str(statement.get("value") or "").strip():
        return None
    return statement


def _material(evidence: dict) -> dict:
    """S2: the tissue, cell type or material the samples came from, as stated and as deposited."""
    statement = paper_statement(evidence, "sample_material")
    if statement is None:
        stated = _open(
            "bioAF holds no statement of the tissue, cell type or material the relevant samples came from",
            scope="the paper's samples section",
            next_action="read the paper's samples section for the material it used",
        )
    else:
        stated = _finding(
            VERIFIED,
            f"the paper identifies the material its relevant samples came from: {statement['value']}",
            scope=str(statement.get("scope") or "the paper's samples section"),
            method=str(statement.get("method") or MEASUREMENT),
            evidence={"quote": statement.get("quote"), "citations": statement.get("citations")},
        )
    pairs = deposit_samples(evidence)
    named = sorted({str(s.get("source_name") or "").strip() for _d, s in pairs} - {""})
    if statement is None or not pairs:
        limitations = record_limitations(evidence)
        return {
            "S2.A": stated,
            "S2.B": _open(
                "; ".join(sorted(set(limitations)))
                or (
                    "bioAF holds no sample records to compare the paper's stated material with"
                    if not pairs
                    else "bioAF holds no statement of the paper's material to compare the records with"
                ),
                scope=_records_scope(pairs) if pairs else "no sample record bioAF could read",
                next_action="acquire the deposit's sample metadata, and record the material the paper states",
            ),
        }
    if not named:
        return {
            "S2.A": stated,
            "S2.B": _open(
                "every sample record bioAF read names no material",
                scope=_records_scope(pairs),
                next_action="read the deposit's sample metadata again",
            ),
        }
    wanted = _norm_organism(statement["value"])
    agreeing = [name for name in named if _norm_organism(name) in wanted or wanted in _norm_organism(name)]
    if agreeing:
        return {
            "S2.A": stated,
            "S2.B": _finding(
                VERIFIED,
                f"the deposit's own sample records name {', '.join(sorted(set(agreeing)))}, the material the "
                "paper states",
                scope=_records_scope(pairs),
            ),
        }
    # A paper's experiments legitimately use different materials, and a name is not a taxonomy, so a
    # difference here is a question for the reader rather than a contradiction bioAF established.
    return {
        "S2.A": stated,
        "S2.B": _open(
            f"the paper states {statement['value']} and the deposit's records name {', '.join(named)}; bioAF "
            "cannot establish from the names alone whether these are the same material or two experiments",
            scope=_records_scope(pairs),
            next_action="check which experiment these records belong to, and what material the paper used for it",
        ),
    }


def _replication(evidence: dict) -> dict:
    """S5.A: whether the replication and pairing the design needs is described anywhere held."""
    statement = paper_statement(evidence, "replication")
    if statement is not None:
        return {
            "S5.A": _finding(
                VERIFIED,
                f"the paper describes its replication: {statement['value']}",
                scope=str(statement.get("scope") or "the paper's design"),
                method=str(statement.get("method") or MEASUREMENT),
                evidence={"quote": statement.get("quote"), "citations": statement.get("citations")},
            )
        }
    pairs = deposit_samples(evidence)
    keyed = sorted(
        {
            key
            for _d, sample in pairs
            for key in (sample.get("characteristics") or {})
            if key in ("replicate", "biological replicate", "technical replicate", "batch", "pair", "donor", "subject")
        }
    )
    if keyed:
        return {
            "S5.A": _finding(
                VERIFIED,
                f"the deposited records describe the design's units, keyed as {', '.join(keyed)}",
                scope=_records_scope(pairs),
            )
        }
    return {
        "S5.A": _open(
            "nothing bioAF holds describes biological versus technical replication, or the pairing this "
            "design requires",
            scope=_records_scope(pairs) if pairs else "the paper's design",
            next_action="read the paper's design for its replication, and record the passage it rests on",
        )
    }


def _references(experiments: list[dict]) -> dict:
    """M2: the result-sensitive references a paper's numbers depend on, and whether they can be recovered.

    plan_8_5 section 3.3. B asks whether the paper specified its reference versions and identifiers
    well enough to recover the inputs it used. That is a question about the PAPER. A release bioAF
    cannot supply is still a release the paper named, and bioAF choosing a pinned default in its
    place is a fact about this run, not a defect in the reporting. What fails B is the paper's own
    statements naming two different references; what leaves it open is bioAF never reading one.
    """
    references = [(e.get("id"), (e.get("reference") or {})) for e in experiments]
    relevant = [(eid, r) for eid, r in references if r]
    if not relevant:
        return {
            "M2.A": _open(
                "bioAF's read recorded no reference inputs for the relevant experiments",
                scope="the reported experiments",
                next_action="read the paper's methods again",
            )
        }
    scope = f"{len(relevant)} reported {'experiment' if len(relevant) == 1 else 'experiments'}"
    stated = [
        (eid, r)
        for eid, r in relevant
        if str((r.get("assembly") or {}).get("stated") or "").strip()
        or str((r.get("annotation") or {}).get("stated") or "").strip()
    ]
    if stated:
        words = ", ".join(
            sorted(
                {
                    str((r.get("assembly") or {}).get("stated") or (r.get("annotation") or {}).get("stated"))
                    for _, r in stated
                }
            )
        )
        identified = _finding(
            VERIFIED,
            f"the paper names the reference its results depend on ({words})",
            scope=scope,
        )
    else:
        identified = _open(
            "the paper names no reference for the relevant experiments",
            scope=scope,
            next_action="read the paper's methods again",
        )
    return {"M2.A": identified, "M2.B": _reference_recoverability(relevant, scope)}


# What each recorded status says about the PAPER's statement, as `validation_reference` sets them.
_USABLE, _UNAVAILABLE, _UNRESOLVED, _NOT_READ, _UNSTATED = (
    "usable",
    "unavailable",
    "unresolved",
    "not_read",
    "unstated",
)
_REFERENCE_PARTS = ("assembly", "annotation")


def _reference_recoverability(relevant: list[tuple], scope: str) -> dict:
    """M2.B: whether every result-sensitive reference the paper states resolves to one identifier."""
    conflicts: list[str] = []
    open_parts: list[str] = []
    recovered: list[str] = []
    for _eid, reference in relevant:
        for name in _REFERENCE_PARTS:
            part = reference.get(name) or {}
            status = str(part.get("status") or "").strip().lower()
            statement = str(part.get("stated") or "").strip()
            if part.get("conflict"):
                conflicts.append(str(part.get("reason") or f"the paper's {name} statements disagree"))
            elif status in (_USABLE, _UNAVAILABLE) and statement:
                # UNAVAILABLE means bioAF cannot SUPPLY what the paper named. The paper named it.
                recovered.append(statement)
            elif status == _NOT_READ:
                open_parts.append(f"the paper's {name} was not read")
            elif statement and status == _UNRESOLVED:
                open_parts.append(f"the paper's {name} ('{statement}') names nothing bioAF recognises")
            else:
                open_parts.append(f"bioAF's read recorded no {name} release for this experiment")
    if conflicts:
        return _finding(
            FAILED,
            "; ".join(sorted(set(conflicts))),
            scope=scope,
            impact=(
                "the reference the paper's results depend on cannot be recovered, because the paper's own "
                "statements name two different ones"
            ),
        )
    if open_parts:
        return _open(
            "; ".join(sorted(set(open_parts))),
            scope=scope,
            next_action="record the reference release the authors used, with the evidence for it",
        )
    return _finding(
        VERIFIED,
        "the paper states a recoverable release for every result-sensitive reference ("
        + ", ".join(sorted(set(recovered)))
        + ")",
        scope=scope,
    )


def _decision_criteria(claims: list[dict], contrasts: list[dict], design: dict | None) -> dict:
    """M4: the decision criteria a paper's results rest on, and whether their reading is unambiguous.

    plan_8_5 section 3.3. Both obligations are read from the normalized scientific predicate, never
    from the shape of an extraction field:

    - **A** asks whether the definitions THIS analysis applies are stated. ``analysis_cutoffs`` is
      the same normalization an analysis would use: raw versus adjusted significance with its
      correction, the effect threshold on the log2 scale, and a refusal naming what is missing. The
      extraction's ``thresholds`` pair exists on every contrast and holds ``None`` where the paper
      stated nothing, so its presence establishes nothing; an analysis that applies no fold-change
      requirement is not missing one; and a legitimate zero is a stated threshold.
    - **B** asks for the reading: direction, scale, contrast orientation and interpretation. It is
      established from the predicate bioAF built for each claim. A claim carrying no predicate
      establishes nothing, and its absence is never proof that nothing is ambiguous.
    """
    return {
        "M4.A": _criteria_stated(contrasts, design),
        "M4.B": _criteria_reading(claims, contrasts),
    }


def _contrast_name(contrast: dict, index: int) -> str:
    return str(contrast.get("name") or "").strip() or f"comparison {index + 1}"


def _criteria_stated(contrasts: list[dict], design: dict | None) -> dict:
    from app.services.validation_claim_cutoffs import analysis_cutoffs

    scope = f"{len(contrasts)} {'contrast' if len(contrasts) == 1 else 'contrasts'}"
    if not contrasts:
        return _open(
            "bioAF's read recorded no comparison whose decision criteria could be checked",
            scope="no contrast",
            next_action="read the paper's design again",
        )
    applied: list[str] = []
    refused: list[str] = []
    for index, contrast in enumerate(contrasts):
        cutoffs = analysis_cutoffs(contrast, design or {})
        if cutoffs.get("refusal"):
            refused.append(f"{_contrast_name(contrast, index)}: {cutoffs['refusal']}")
        elif cutoffs.get("statement"):
            applied.append(f"{_contrast_name(contrast, index)}: {cutoffs['statement']}")
    if refused:
        return _open(
            "; ".join(refused),
            scope=scope,
            next_action="record the cutoff from the paper's methods, with its quote",
        )
    return _finding(
        VERIFIED,
        "every comparison the paper's claims rest on states the criteria it applies (" + "; ".join(applied) + ")",
        scope=scope,
    )


_ORIENTATIONS = {"test_over_reference": "the test arm over the reference arm"}


def _reading_words(detail: dict) -> str:
    """One claim's established reading, in the words the predicate itself carries."""
    from app.services.validation_claim_cutoffs import describe_cutoff

    parts = []
    significance = detail.get("significance") or {}
    if significance.get("kind"):
        adjustment = significance.get("adjustment")
        parts.append(describe_cutoff(significance) + (f" ({adjustment})" if adjustment else ""))
    effect = detail.get("effect") or {}
    if effect.get("kind") == "none":
        parts.append("no fold-change requirement")
    elif effect.get("kind"):
        parts.append(describe_cutoff(effect))
    direction = str(detail.get("direction") or "either").strip()
    parts.append("either direction" if direction == "either" else direction)
    orientation = _ORIENTATIONS.get(str(detail.get("orientation") or ""))
    if orientation:
        parts.append(orientation)
    return ", ".join(parts)


def _criteria_reading(claims: list[dict], contrasts: list[dict]) -> dict:
    scope = f"{len(claims)} {'claim' if len(claims) == 1 else 'claims'}"
    readings: list[str] = []
    open_reasons: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        detail = claim.get("predicate_detail")
        if ((claim.get("consistency") or {}).get("filter_semantics") or {}).get("unresolved"):
            open_reasons.append("the signed or absolute reading of a claim's filter is unresolved")
        if not isinstance(detail, dict):
            continue
        if str(detail.get("status") or "").strip().lower() == "resolved":
            readings.append(_reading_words(detail))
        else:
            open_reasons.append(str(detail.get("reason") or "a claim's reading could not be established"))
    if not readings and not open_reasons:
        return _open(
            "bioAF built no reading of the paper's decision criteria to check",
            scope=scope,
            next_action="read the paper's claims again, or record which reading the paper meant",
        )
    if open_reasons:
        return _open(
            "; ".join(sorted(set(open_reasons))),
            scope=scope,
            next_action="record which reading the paper meant, with the evidence for it",
        )
    return _finding(
        VERIFIED,
        "every claim's reading is established: " + "; ".join(sorted(set(readings))),
        scope=scope,
    )


def _author_results(claims: list[dict], inventory: dict | None) -> dict:
    """R1: each allocated claim check, from the comparison against the authors' own published table."""
    from app.services.validation_rubric_v3 import result_allocation

    by_index = {c.get("index"): c for c in claims if isinstance(c, dict) and isinstance(c.get("index"), int)}
    if not by_index:
        by_index = {position: c for position, c in enumerate(claims) if isinstance(c, dict)}
    assessed: dict[str, dict] = {}
    for part in result_allocation(inventory)["R1"]:
        claim = by_index.get(part["claim_index"]) or {}
        outcome = ((claim.get("consistency") or {}).get("outcome")) if claim else None
        reason = str((claim.get("consistency") or {}).get("reason") or "").strip() if claim else ""
        leaf_id = f"R1.{part['id']}"
        if outcome == _AGREES:
            assessed[leaf_id] = _finding(
                VERIFIED,
                reason or "the authors' own published table agrees with this claim under its stated predicate",
                scope=part["subject"],
            )
        elif outcome == _DISAGREES:
            assessed[leaf_id] = _finding(
                FAILED,
                reason or "the authors' own published table disagrees with this claim under its stated predicate",
                scope=part["subject"],
                impact="the paper's text and its own published results do not state the same number",
            )
        else:
            assessed[leaf_id] = _open(
                reason or "this claim has not been compared with the authors' published results",
                scope=part["subject"],
                next_action="check this claim against the table the paper binds it to",
            )
    return assessed


# ---- applicability -------------------------------------------------------------------------------
#
# plan_8_4 section 3.5: applicability is about the PAPER's work, never about bioAF's adapters. A
# criterion is excluded only where cited evidence establishes that the paper's methods have no
# counterpart for it, and the exclusion redistributes its weight so the profile still totals 100.
#
# The one exclusion bioAF can establish deterministically today: a reference genome or annotation has
# no counterpart in an analysis that sequences nothing. A western blot, a live-cell tracking assay, an
# immunofluorescence image and a qRT-PCR reaction have feature definitions (an antibody, a primer pair)
# and no genomic reference to state, and requiring one of them would be requiring a fact that does not
# exist. This is deliberately NOT keyed on whether bioAF has an adapter: an assay bioAF cannot execute
# still has a reference to state, and an unsupported assay is a limitation of bioAF's, not the paper's.

_NON_GENOMIC = (
    "western blot",
    "immunoblot",
    "immunofluorescence",
    "immunohistochem",
    "microscopy",
    "imaging",
    "tracking",
    "qrt-pcr",
    "qpcr",
    "rt-pcr",
    "elisa",
    "flow cytometry",
    "atomic force microscopy",
    "electrophysiolog",
    "patch clamp",
    "mass spectrometr",
)


def _is_non_genomic(assay: str) -> bool:
    text = (assay or "").strip().lower()
    return bool(text) and any(word in text for word in _NON_GENOMIC)


def profile_for(*, plan: dict | None) -> dict:
    """The applicability profile for this paper: the default, with what its methods have no counterpart
    for excluded, each exclusion carrying the evidence that establishes it.

    Uncertainty never excludes. A paper whose assays bioAF could not identify keeps every criterion, so
    a hard check can never be dropped by failing to read the paper well enough to name its methods.
    """
    from app.services.validation_rubric_v3 import ApplicabilityUncertain, default_profile

    experiments = [e for e in (plan or {}).get("reported_experiments") or [] if isinstance(e, dict)]
    assays = [str(e.get("assay") or "").strip() for e in experiments]
    named = [a for a in assays if a]
    exclusions = []
    if named and len(named) == len(assays) and all(_is_non_genomic(a) for a in named):
        exclusions.append(
            {
                "criterion": "M2",
                "rationale": (
                    "every experiment this paper reports measures something other than sequence ("
                    + ", ".join(sorted(set(named)))
                    + "), so there is no result-sensitive genome or annotation release for it to state"
                ),
                "source": "the assays the paper's own methods state for each reported experiment",
            }
        )
    try:
        return default_profile(exclude=exclusions)
    except ApplicabilityUncertain:
        # An exclusion that cannot be made leaves the allocation where it is, which is the safe
        # direction: a criterion that stays allocated is grey, and grey costs nothing.
        return default_profile()
