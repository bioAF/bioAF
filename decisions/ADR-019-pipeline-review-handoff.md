# ADR-019: Pipeline Run Review and Data Handoff Protocol

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

In bioAF's target teams, there is a natural division of labor: bioinformaticians (Jake) run alignment pipelines and perform QC, then computational biologists (Sarah) take the validated outputs for downstream analysis. This handoff: the moment when processed data transitions from "being validated" to "ready for analysis", is one of the highest-friction points in the workflow.

Today, this handoff is invisible in bioAF. The experiment status machine transitions from `processing` to `analysis` automatically when a pipeline completes. This is a *technical* signal (the pipeline didn't crash) not a *scientific* signal (a human evaluated the results and judged them fit for downstream use). The consequence:

- Sarah doesn't know whether Jake has reviewed the QC dashboard yet.
- Jake's QC verdict (which samples are good, which are borderline, which should be excluded) lives in Slack messages, verbal conversations, or nowhere at all.
- When Jake goes on vacation and forgets to communicate, Sarah is blocked: not by a technical barrier, but by uncertainty about whether the data is trustworthy.
- When Maria asks "was this data reviewed before analysis?" during a lab meeting or publication review, the answer requires forensic investigation.

Competing approaches considered:

1. **Hard gate (ACL-based).** Sarah cannot access pipeline outputs until Jake approves. Rejected: creates real blocking when Jake is unavailable, adds friction to exploratory work, and punishes teams for normal human forgetfulness (vacation, sick days, Friday afternoons).
2. **No formal mechanism.** Rely on team communication. Rejected: this is the status quo and it's the problem we're solving.
3. **Advisory signal.** Data is always accessible. Review status is a visible, auditable label attached to the pipeline run and its outputs. Chosen: makes the right behavior effortless and the wrong behavior visible without creating barriers.

## Decision

bioAF introduces a lightweight review protocol for pipeline runs. Reviews are advisory: they inform but do not gate access. The review status is visible everywhere pipeline run outputs appear. The review action triggers notifications to downstream consumers (computational biologists and experiment owners).

### Design Principle

**Always accessible, never ambiguous.** Any authorized user can access any data at any time regardless of review status. But the data carries its own status visibly: like a label, so that anyone working with it knows whether it has been reviewed, by whom, and with what verdict.

### Review Record

```sql
pipeline_run_reviews (
    id SERIAL PRIMARY KEY,
    pipeline_run_id INTEGER NOT NULL REFERENCES pipeline_runs(id),
    reviewer_user_id INTEGER NOT NULL REFERENCES users(id),
    verdict VARCHAR(20) NOT NULL,        -- 'approved', 'approved_with_caveats', 'needs_reprocessing'
    notes TEXT,                           -- free-text review notes (caveats, exclusion recommendations, etc.)
    sample_verdicts_json JSONB,           -- per-sample verdicts: {"sample_id": {"verdict": "exclude", "reason": "low viability"}}
    recommended_exclusions INTEGER[],     -- array of sample IDs the reviewer recommends excluding
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_by_id INTEGER REFERENCES pipeline_run_reviews(id),  -- if the run is re-reviewed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

A pipeline run can have multiple reviews (e.g., Jake reviews initially, Sarah re-reviews after deeper inspection). The most recent non-superseded review is the active one. Previous reviews are preserved in the audit trail.

### Review Verdicts

| Verdict | Meaning | UI Badge | Notification |
|---|---|---|---|
| `approved` | Data is good. Proceed with downstream analysis. | Green checkmark | "Experiment X pipeline run ready for analysis" |
| `approved_with_caveats` | Data is usable but with noted issues. Reviewer provides specific guidance. | Yellow checkmark | "Experiment X pipeline run ready with caveats" (caveats included in notification body) |
| `needs_reprocessing` | Data quality is insufficient. Re-run recommended with different parameters. | Red flag | "Experiment X pipeline run flagged for reprocessing" |

An unreviewed pipeline run shows no badge: a neutral, distinct-from-all-three-verdicts state.

### Review Workflow

The review flow is intentionally lightweight: one screen, one click, optional notes:

1. Pipeline completes. QC dashboard auto-generates (existing behavior, F-051).
2. Jake opens the QC dashboard from the experiment detail page or from the notification he received about pipeline completion.
3. Jake reviews the metrics: cell count, reads/cell, genes/cell, mitochondrial %, knee plot, doublet scores.
4. Jake clicks "Submit Review" on the QC dashboard page (or on the pipeline run detail page).
5. A review panel expands:
   - **Verdict selector:** Three options (approved / approved with caveats / needs reprocessing).
   - **Notes field:** Free text. Pre-populated with a template: "Samples reviewed: [list]. Recommended exclusions: [none]. Notes: [none]."
   - **Per-sample QC quick-update:** A compact table of all samples in the run, each with a pass/warning/fail toggle (pre-populated from existing sample QC flags). Jake can update sample QC flags inline as part of the review: no need to navigate to a separate page.
   - **Submit button.**
6. On submit:
   - The review record is created in `pipeline_run_reviews`.
   - Sample QC flags are updated if Jake changed any.
   - The audit log records the review action with full details.
   - Notifications fire to: the experiment owner, all users with comp_bio role in the org, and anyone who has "watched" the experiment.

### Visibility: Where the Badge Appears

The review status badge appears in every context where pipeline run outputs are referenced:

- **Experiment detail page → Pipeline Runs tab.** Each run shows its review badge. Clicking the badge opens the review details (verdict, notes, reviewer, timestamp).
- **Experiment detail page → Data tab.** Files produced by unreviewed or flagged runs are visually distinguished (e.g., muted opacity, with a tooltip: "This file was produced by an unreviewed pipeline run").
- **Dataset browser (F-011).** The review status is a filterable field. Sarah can filter to "show only experiments with approved pipeline runs."
- **Data selector in pipeline launcher and notebook launcher.** When selecting input data for a new pipeline run or notebook session, files from unreviewed or flagged runs show the badge. Not blocked: just flagged.
- **Provenance view (F-072).** The pipeline run node in the provenance graph shows the review badge. Caveats are visible on hover.
- **QC dashboard (F-051).** The review verdict is displayed at the top of the dashboard, alongside the "Plain English" summary.

### Notification as Handoff

The review submission is the formal handoff event. When Jake submits a review with verdict `approved` or `approved_with_caveats`, the notification serves as the "data is ready" signal that today lives in Slack messages and verbal conversations.

The notification includes:

- Experiment name and pipeline run identifier
- Reviewer name
- Verdict
- Caveats / notes (if any)
- Recommended sample exclusions (if any)
- Direct link to the QC dashboard and the experiment detail page

This means Jake never needs to separately communicate the handoff. The review action *is* the communication. If Jake forgets to review before vacation, the data is still accessible: it just shows as "unreviewed," which is itself a signal.

### Experiment Status Machine Update

The experiment status machine (ADR-006) gains awareness of reviews:

```text
processing → pipeline_complete → reviewed → analysis → complete
                                    ↑
                              (re-review)
```

- `pipeline_complete` is a new status that replaces the current auto-transition to `analysis`. It means "a pipeline finished but no human has reviewed the results yet."
- `reviewed` is set when the first review with verdict `approved` or `approved_with_caveats` is submitted.
- The transition from `pipeline_complete` to `analysis` still happens, but now it's triggered by the review, not by the pipeline completion.
- **Critical: this is a status update, not a gate.** If Sarah launches a notebook from an experiment in `pipeline_complete` status, it works. The status is informational.
- If no review is submitted within a configurable threshold (default: 72 hours), a reminder notification is sent to the pipeline submitter and the experiment owner.

### API Endpoints

```text
POST   /api/v1/pipeline-runs/{id}/reviews         → Submit a review
GET    /api/v1/pipeline-runs/{id}/reviews          → List all reviews for a run
GET    /api/v1/pipeline-runs/{id}/review           → Get the active (most recent non-superseded) review
PUT    /api/v1/pipeline-runs/{id}/reviews/{rid}    → Supersede a review (marks old one as superseded, creates new)
```

### Permissions

- Any user with `comp_bio` or `admin` role can submit a review.
- `bench` users can view reviews but not submit them (they see the badge and the notes but don't have the review button).
- The experiment owner (any role) receives review notifications regardless of their role.

## Rationale

- **Advisory over gate because humans forget things.** The vacation scenario is not an edge case: it's a regular occurrence. A hard gate that blocks Sarah because Jake forgot to click a button before a long weekend is worse than no system at all. Advisory signals make the right behavior easy and the wrong behavior visible, then trust teams to follow their own processes.
- **The review action replaces informal communication.** Today, the handoff is a Slack message, a verbal comment, or nothing. Making the review a first-class platform action means the communication is structured, auditable, and permanently attached to the data it describes. Maria never has to ask "was this reviewed?": the answer is visible in the provenance view.
- **Per-sample verdicts carry the nuance.** "Approved" is insufficient for real data. Jake almost always has sample-level notes: "sample 14 is borderline, sample 22 had a strange doublet profile." Capturing this inline with the review, rather than in a separate QC flag update workflow, means the context is never lost.
- **72-hour reminder nudge catches the forgetfulness case.** If Jake forgets to review, a gentle reminder after three days is more productive than blocking Sarah. The reminder is configurable: teams that want tighter turnaround can set it to 24 hours; teams that are more relaxed can set it to a week or disable it.
- **Minimal new surface area.** One new table, one API endpoint, one UI component (the review panel), one badge component, one notification trigger. Everything else: QC dashboard, sample QC flags, notification system, audit log, already exists. This feature is assembled from existing parts.

## Consequences

- The `pipeline_run_reviews` table is added to the PostgreSQL schema.
- The experiment status machine gains the `pipeline_complete` state between `processing` and `reviewed`. Existing experiments in `analysis` status are unaffected (backward compatible).
- The QC dashboard (F-051) gains a "Submit Review" action. This is the primary review entry point.
- The pipeline run detail page gains a review summary section.
- The badge component is reusable and appears in five+ UI locations (listed above). It should be built as a shared component.
- The notification service (ADR-010) gains a new event type: `pipeline_run_reviewed`. The event routing must support the "experiment watchers" concept: users who have opted in to notifications for a specific experiment.
- A background job checks for unreviewed pipeline runs older than the configured threshold and sends reminder notifications. This uses the existing notification infrastructure.
- The dataset browser (F-011) gains a "review status" filter dimension.
- Future extension: review templates for recurring experiment types (pre-populated checklists of what to verify), similar to how experiment templates work for metadata.
