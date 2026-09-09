"""change_7.2 section 1: one route policy, shared by both approval entrances.

Study 32 was refused its route by the driver and reached a stated outcome. Study 33 was approved by
hand against the same capability block, byte for byte, and walked onto a route that could never be
taken, because `approve_plan` validated a deposit conflict and a species mismatch and had no
route-feasibility equivalent. Which door a study came through decided whether its route was checked
at all.

**Four independent questions, never collapsed into one answer.**

1. *Does an adapter exist for this operation?* A fact about bioAF's own registry. `supported` means
   this and only this: it is never a function of an organization or a dataset, so it can never be an
   UNKNOWN misread as a NO.
2. *Do suitable inputs exist in the resource?* What the deposit actually holds.
3. *Can this organization reach them?* Access class, credentials, per-dataset authorization.
4. *Did discovery establish any of the above, or did it fail to look?* A failure to look is not an
   absence.

**The three terminal refusals must stay apart.** A missing adapter is a limit of bioAF and must
never read as an omission by the authors; a missing input is a fact about the deposit and must
never read as a software limitation; a missing authorization is a fact about this organization and
must never read as a feature request. One wording for all three tells a reader nothing about which
of them to act on.

**`undetermined` authorizes a bounded attempt, not an indefinite one.** "Never refuse on an unknown"
means an unknown must not be recorded as an established absence. It does not mean an unknown
licenses unbounded retrying: section 3 bounds the attempts, and an exhausted discovery completes the
assessment carrying an unresolved discovery limitation rather than hardening into an inferred
absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROCEED = "proceed"
UNDETERMINED = "undetermined"
CONTESTED = "contested"
NO_ADAPTER = "no_adapter"
NO_INPUT = "no_input"
NOT_AUTHORIZED = "not_authorized"

ACTIONS = (PROCEED, UNDETERMINED, CONTESTED, NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED)

# The actions that end the execution attempt. Owner decision 1: none of them has an override.
# `deposit_override` and `species_override` answer a contested scientific judgment, which is a
# different thing from a fact about the registry, the deposit or the organization.
TERMINAL_ACTIONS = (NO_ADAPTER, NO_INPUT, NOT_AUTHORIZED)
OVERRIDABLE_ACTIONS = (CONTESTED,)
# What authorizes the route to be attempted. `undetermined` is here because refusing on an unknown
# would hide a workable route behind a timeout.
EXECUTING_ACTIONS = (PROCEED, UNDETERMINED)

# What each route needs the paper to have, and how to say it to a reader.
ROUTE_REQUIREMENTS: dict[str, str] = {"deposit": "preprocessed_data", "pipeline": "raw_data"}
ROUTE_NEEDS: dict[str, str] = {
    "deposit": "pre-processed data to reproduce the finding from",
    "pipeline": "raw sequencing reads to fetch and re-run",
}

# Precedence when legs disagree. A contested judgment is the reader's to answer, so it is reported
# first; a terminal refusal outranks an unknown, because something WAS established.
_PRECEDENCE = (CONTESTED, NO_ADAPTER, NOT_AUTHORIZED, NO_INPUT, UNDETERMINED, PROCEED)


@dataclass(frozen=True)
class RouteFinding:
    """What one leg of a route concluded, and about which resource."""

    leg: str
    action: str
    reason: str
    resource: str | None = None
    archive: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    route: str
    action: str
    reason: str
    findings: tuple[RouteFinding, ...] = field(default_factory=tuple)

    @property
    def authorizes_execution(self) -> bool:
        """Whether the route may be attempted. False for a contested judgment and every terminal."""
        return self.action in EXECUTING_ACTIONS

    @property
    def overridable(self) -> bool:
        return self.action in OVERRIDABLE_ACTIONS

    @property
    def terminal(self) -> bool:
        return self.action in TERMINAL_ACTIONS

    def as_record(self) -> dict:
        """The decision as it is stored on the study, so a report can name the axis that refused."""
        return {
            "route": self.route,
            "action": self.action,
            "reason": self.reason,
            "findings": [
                {
                    "leg": f.leg,
                    "action": f.action,
                    "reason": f.reason,
                    "resource": f.resource,
                    "archive": f.archive,
                }
                for f in self.findings
            ],
        }


def legs_for(route: str) -> tuple[str, ...]:
    """The route legs a choice actually commits to. ``both`` commits to two."""
    if route == "both":
        return ("deposit", "pipeline")
    return (route,)


def decide_route(
    *,
    route: str,
    capabilities: dict | None,
    conflict: dict | None = None,
    species_hold: str | None = None,
    deposit_override: bool = False,
    species_override: bool = False,
) -> RouteDecision:
    """The one answer both approval entrances take.

    ``conflict`` and ``species_hold`` are the two contested scientific judgments the C1 gate already
    refuses on; they are passed in rather than re-derived because re-deriving them here would put a
    network fetch in the way of every approval and let an outage decide the answer.
    """
    caps = capabilities or {}

    if conflict and not deposit_override:
        reason = str(conflict.get("message") or "the deposit contradicts the plan")
        return _single(route, CONTESTED, reason)
    if species_hold and not species_override:
        return _single(route, CONTESTED, str(species_hold))

    deposits = [d for d in (caps.get("deposits") or []) if isinstance(d, dict)]
    findings = tuple(_decide_leg(leg, caps, deposits) for leg in legs_for(route))
    action = _governing(findings)
    reason = " ".join(f.reason for f in findings if f.action != PROCEED).strip()
    return RouteDecision(route=route, action=action, reason=reason, findings=findings)


def _single(route: str, action: str, reason: str) -> RouteDecision:
    findings = tuple(RouteFinding(leg=leg, action=action, reason=reason) for leg in legs_for(route))
    return RouteDecision(route=route, action=action, reason=reason, findings=findings)


def _governing(findings: tuple[RouteFinding, ...]) -> str:
    for action in _PRECEDENCE:
        if any(f.action == action for f in findings):
            return action
    return PROCEED


def _decide_leg(leg: str, caps: dict, deposits: list[dict]) -> RouteFinding:
    key = ROUTE_REQUIREMENTS.get(leg)
    if key is None:
        return RouteFinding(leg=leg, action=PROCEED, reason="")
    need = ROUTE_NEEDS[leg]
    answer_row = caps.get(key) or {}
    answer = answer_row.get("value")

    if answer not in ("yes", "no"):
        # Axis 4. Nothing was established, so nothing may be concluded. The attempt is still
        # authorized, and section 3 bounds it.
        detail = answer_row.get("failure_reason") or f"bioAF could not establish whether {need} is published"
        return RouteFinding(leg=leg, action=UNDETERMINED, reason=detail)

    holders = [d for d in deposits if d.get(key) == "yes"]

    if answer == "no":
        # Axis 2, scoped to the deposits that were actually read. "for this paper" states something
        # about the publication that a deposit listing cannot establish, and study 32 said exactly
        # that while holding the results table that disproved it.
        named = ", ".join(str(d.get("accession")) for d in deposits) or "the deposits bioAF found"
        return RouteFinding(
            leg=leg,
            action=NO_INPUT,
            reason=f"{named} publishes no {need}.",
            resource=named,
        )

    if not holders:
        # Discovery answered yes without naming a deposit that holds it. Nothing refuses the route.
        return RouteFinding(leg=leg, action=PROCEED, reason="")

    if any(d.get("supported") == "yes" and d.get("access") != "controlled" for d in holders):
        return RouteFinding(leg=leg, action=PROCEED, reason="")

    # Axis 1 before axis 3. Both can be true of one deposit, and the adapter is the axis bioAF owns:
    # telling a lab to negotiate data access for what is a capability gap sends it to the wrong
    # remedy, and the reverse files a feature request for a permission problem.
    unsupported = [d for d in holders if d.get("supported") != "yes"]
    if unsupported:
        return RouteFinding(
            leg=leg,
            action=NO_ADAPTER,
            reason=_no_adapter_reason(unsupported, need),
            resource=str(unsupported[0].get("accession") or "") or None,
            archive=str(unsupported[0].get("archive") or "") or None,
        )

    controlled = [d for d in holders if d.get("access") == "controlled"]
    return RouteFinding(
        leg=leg,
        action=NOT_AUTHORIZED,
        reason=_not_authorized_reason(controlled, need),
        resource=str(controlled[0].get("accession") or "") or None,
        archive=str(controlled[0].get("archive") or "") or None,
    )


def _describe(deposits: list[dict]) -> str:
    return ", ".join(
        f"{d.get('accession')} ({str(d.get('archive') or 'archive').upper()}, {d.get('access')} access)"
        for d in deposits
    )


def _no_adapter_reason(deposits: list[dict], need: str) -> str:
    """A limit of bioAF, stated as one. The authors published the data; the obstacle is ours, and a
    reader who sees only "cannot run" will read it as a fault of the paper."""
    archives = sorted({str(d.get("archive") or "that archive").upper() for d in deposits})
    return (
        f"This paper published {need}, in {_describe(deposits)}. bioAF has no adapter for "
        f"{' or '.join(archives)}, so it cannot acquire this data. The data is published; the "
        "limitation is bioAF's."
    )


def _not_authorized_reason(deposits: list[dict], need: str) -> str:
    """A fact about this organization's authorization, never about bioAF's features.

    The remedy is a data access request, so the reason names what a lab would take to one: the
    accession and the archive holding it.
    """
    described = _describe(deposits)
    return (
        f"This paper published {need}, in {described}. bioAF can read this archive, and this "
        "organization is not authorized to reach this dataset. Access is granted per dataset by its "
        "data access committee; bioAF needs approved credentials for it before the route can run."
    )
