# Literature and AI Workflows

Literature Library, AI Literature Review, Agent Review, the conversational assistant and Paper Validation
have different outputs and approval boundaries. Availability depends on permissions, provider configuration,
component readiness and beta settings.

## Literature Library

Open **Lab Knowledge > Literature Library** to ingest/search Papers, associate them with experiments or
Projects, manage reading state and add comments. Searchable experiment filters page through eligible
experiments rather than treating the first 100 records as the complete list.

Bibliographic sources include PubMed, bioRxiv, Europe PMC, Semantic Scholar and OpenAlex. Configure source
credentials and rates on **Literature Library > Sources** (`/lab-knowledge/literature/sources`). OpenAlex is always on; its enabled flag cannot be cleared.
A positive rate override replaces the source's default requests-per-second limit. Zero or null resets to
the default; omitting the field leaves it unchanged. Shared dispatch pacing counts source subrequests and
retries, and applies bounded retry/deadline rules. A remote throttle is not a successful empty search.

## AI Literature Review

Choose an experiment and start a review to generate and score candidate-paper recommendations. The current
review-run path automatically accepts up to its configured maximum at or above the score threshold into the
Library and associates them with that experiment. Review those Papers and their recorded reasoning;
do not assume every recommendation waits for a manual acceptance decision.

The backend distinguishes:

- **Complete:** source collection and ranking completed; zero recommendations can be a valid result.
- **Partial:** some source/query attempts failed while others succeeded.
- **Failed:** collection or ranking could not produce a valid outcome.
- **Cancelled:** execution was cancelled and its owned source work was stopped.

An invalid model response is not treated as a clean empty recommendation list. Inspect the run's status
and available diagnostic details; not every launcher view shows the full stored reason. After a timeout
or provider error, check existing work before starting another review.

## Agent Review

Agent Review produces severity-coded advisory notes for runs/experiments and comparisons. It does not
change the scientific record by itself or replace a scientist's review/handoff decision. The review artifact
contains selected run metadata, QC and context for inference; optional literature inputs add associated
Paper context. Administrators should review the enabled inputs and active provider before sending data.

Hosted integrations include OpenAI, Anthropic and Google. Configure the active provider and select a model
that supports the chosen workflow, including any required tool API.
The application validates provider envelopes and surfaces controlled errors for malformed or failed calls.

## Conversational Assistant

Open the floating assistant from an available page. It can inspect accessible records and propose actions
through its tool interface. Consequential tools create an approval plan; review the concrete steps before
confirming. Read-only investigation can happen before a plan is approved. Provider/model configuration and
effective permissions still govern each operation.

Hosted providers receive the request context and tool results selected for that workflow. Approval of an
action plan is not a blanket statement that all preceding model requests were offline or free. A provider
failure is an error, not confirmation that a proposed action completed.

## Paper Validation

**Lab Knowledge > Validation Studies** appears when the `lit_validation` beta feature and permissions permit
it. A reproduction plan routes a Paper's accession/assay to a pipeline; inspect its pipeline, inputs,
resources and evidence ceiling before approving compute. See [Paper Validation](paper-validation.md) for
supported assays and known limitations. Literature recommendation scores and advisory Agent Review notes
are different from a completed reproduction result.

## Lab Knowledge Records

Use **Lab Documents**, **Lab Glossary** and **Scientific Decision Records** for durable knowledge. Document
versions, glossary proposals and SDR status/reassessment are explicit records with their own permissions.
An AI suggestion is not automatically an approved glossary entry or a scientific decision.
