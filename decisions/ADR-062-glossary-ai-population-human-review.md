# ADR-062: AI-Assisted Glossary Population with Mandatory Human Review

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

Manually building a lab glossary from scratch is high-friction and unlikely to happen
consistently. LLMs can extract terminology from existing content, but LLM-generated definitions
may be generic, incorrect, or inappropriate. Automatic writes without review would degrade
glossary quality and trust.

The template assumed glossary scans would run as **Kubernetes Jobs**. That assumption does not
match how the codebase actually runs LLM work. The relevant precedents:

- **Agent Review** (ADR-055, `backend/app/services/agent_review_job_service.py`) runs LLM work
  via FastAPI `BackgroundTasks` (`background_tasks.add_task(job_service.execute_hosted, ...)`),
  tracking a job row through `pending → building_artifacts → submitted → succeeded/failed`.
- **Literature review runs** (`backend/app/services/literature/lit_review_run_service.py`) run
  via `asyncio.create_task(_execute_run(run_id))`.
- LLM calls go through the provider abstraction (ADR-052/053): `get_client(provider).submit(prompt, payload, model, api_key)`
  (`backend/app/services/llm_provider_clients/__init__.py`), with per-org config in
  `LlmProviderConfig`.

Kubernetes Jobs in this codebase are reserved for heavy Nextflow pipeline execution
(`backend/app/adapters/compute/kubernetes.py`), not for LLM calls.

## Decision

The glossary supports three AI-assisted population modes: scan a specific document, generate for
a topic, and scan the platform. In all cases the LLM produces **proposals**, not committed
entries, and every proposal passes through a mandatory human review flow before any write.

**Execution mechanism (reconciled):** a glossary scan is an in-process async job following the
Agent Review pattern, **not** a Kubernetes Job:
- A `lab_glossary_scan_jobs` row is created with `status="pending"`.
- Execution is dispatched via `BackgroundTasks` (or `asyncio.create_task` where no request
  `BackgroundTasks` is available), mirroring `agent_review_job_service.execute_hosted`. The job
  fetches source content by `scan_type`, calls `get_client(provider).submit(...)` to extract
  term/definition pairs, deduplicates against `lab_glossary_terms`, cross-references
  `lab_glossary_rejected_proposals`, writes `lab_glossary_scan_proposals`, transitions the job to
  `complete` (or `failed` with `error_message`), and notifies the initiating user via
  `InAppChannel.deliver(...)`.
- A startup reconciler marks orphaned in-flight scan jobs as `failed`, mirroring
  `agent_review_job_service.mark_orphaned_on_startup`.

**Source fetching by scan type:** `document` extracts text from the document's current
`lab_document_versions` GCS file (reusing the existing extraction utilities: `pdfplumber` in
`document_service`, `fitz` in `literature/extraction.py`); `topic` uses the topic string as LLM
context; `platform_wide` collects experiment names/hypotheses/notes, sample metadata, pipeline
run parameters, Lab Knowledge documents, and existing SDR text, chunked before LLM calls.

**Review flow:** new terms and changed terms are presented separately. Per-row actions (Accept,
Reject, and for changed terms Keep Existing) give granular control; a bulk "Accept All Remaining"
commits the rest. Accepted proposals write to `lab_glossary_terms` (with prior values copied to
`lab_glossary_term_history` for changes); rejected and kept-existing proposals write to
`lab_glossary_rejected_proposals` so future scans can surface rejection history via the
`previously_rejected` flag. The review session writes one audit-log summary entry. CSV import
produces the same `lab_glossary_scan_proposals` records (`scan_type="import"`) and uses the same
review flow.

## Rationale

Mandatory human review keeps glossary quality and trust intact while still removing the
blank-page friction of manual entry. Running scans in-process (BackgroundTasks/asyncio) rather
than as K8s Jobs matches every other LLM feature in the codebase, reuses the provider abstraction
and per-org credential handling, and avoids standing up K8s Job manifests for short LLM calls that
do not need a dedicated pod. Reusing the existing PDF/text extraction utilities avoids a duplicate
extraction implementation.

## Consequences

**Positive:**
- Human stays in the loop; glossary quality is maintained.
- Rejection history prevents re-surfacing unwanted proposals without context.
- Consistent review UX for both LLM and CSV import flows.
- Reuses the Agent Review job lifecycle, the LLM provider abstraction, notifications, and
  text-extraction utilities; no new dispatch mechanism.

**Negative:**
- The review step adds friction; admins may skip large scans if the queue is long.
- Platform-wide scans may produce many proposals for established orgs; chunking and the
  dedup/`previously_rejected` logic mitigate noise but do not eliminate it.
- In-process execution shares the API process's resources (consistent with Agent Review); very
  large platform-wide scans should chunk work and yield rather than block.
