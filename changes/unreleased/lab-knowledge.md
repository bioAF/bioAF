### Lab Knowledge

- New top-level Lab Knowledge section with three areas: Documents, Glossary,
  and Decision Records. Everything is org-scoped, permission-gated, audit-logged,
  and surfaced in global and quick search.

#### Lab Documents

- Store operational and institutional documents (manuals, policies, SOPs) with
  controlled tags, upload-new-version history, and archive/restore. Distinct
  from experiment-linked files in Data & Files.
- Add a document either by uploading from your device or by having the server
  pull it from a public URL, matching the Reference Data add flow.
- Open a document to read it inline (paginated PDF viewer, plus image and text
  previews; other types offer a download), and add notes to it, the same way
  comments work on literature papers.

#### Lab Glossary

- Maintain a shared glossary of lab-specific terms with definitions, aliases,
  categories, and context. Add terms manually, import a CSV/TSV, or run an AI
  scan (from a topic, a document, or platform-wide) that uses the org's active
  LLM provider.
- Every AI proposal and CSV import goes through human review before it is
  committed. A banner shows when proposals are awaiting review and opens the
  review flow; rejected proposals are remembered so future scans can flag them.

#### Scientific Decision Records (SDRs)

- Capture significant scientific decisions as numbered records (SDR-001, ...)
  with a decision, justification, owner, category, and a draft -> active ->
  flagged-for-review status workflow, including supersession links between
  records.
- Date-based re-assessment triggers flag a record for review on its due date
  and notify the owner, with a one-time 7-day advance warning.

### Fixes

- Lab document uploads now use an origin-aware resumable upload session, fixing
  a "Failed to fetch" error when sending the file to storage from the browser.
