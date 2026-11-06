### AI Literature Review

- A review whose literature sources all failed now ends as failed instead of complete with no recommendations. When only some sources fail, the review ends as partial, and its error message names each source and how many of its searches failed, for example `some_literature_sources_failed: pubmed 1/2 attempts failed`. Partial reviews keep their recommendations.
- A review can no longer start when the organization has no enabled literature sources. Starting one returns `no_literature_sources_enabled` (HTTP 409) without creating a run. A review whose sources were disabled after it was queued fails before any LLM call, and the automated cadence skips the organization with the same reason.
- A ranking from the LLM that cannot be read (not JSON, not a list, or with no usable entries) now fails the review instead of completing it with no recommendations. A valid empty ranking still counts as a successful review in which nothing was relevant. Malformed scores, such as NaN or out-of-range values, are ignored instead of becoming a perfect score.
- A cancelled review is recorded as failed with the message `cancelled`.
- Every review ends with a status, a completion time, and one terminal notification attempt, including reviews that fail early because their experiment or LLM provider is gone. An error while delivering that notification no longer changes the recorded outcome. The "papers added" notification for scheduled reviews is best effort if the review is cancelled at that moment.
- Review audit entries record per-source search counts and failure types, and record LLM provider failures as `<step>: provider_error (<class>)`, without provider response text or credentials.

### Literature search

- Europe PMC, bioRxiv, Semantic Scholar, and PubMed now report a failed search (such as rate limiting, an outage, or an unreadable response) instead of returning no results, so interactive search marks that source as failed and AI Literature Review counts it as a failed attempt.
- A second copy of a paper that has no DOI and no authors now reuses the existing paper. Previously it raised a database error, which left the interactive search running indefinitely and would have failed an AI Literature Review.
