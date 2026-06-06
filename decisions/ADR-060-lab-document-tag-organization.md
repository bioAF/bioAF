# ADR-060: Tag-Based Document Organization for Lab Knowledge

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

Lab Documents (ADR-059) need an organizational structure that lets users find documents
without knowing exact titles. Traditional folder hierarchies require upfront structure
decisions that become wrong over time and create disputes about where things belong.

The codebase already has a `controlled_vocabularies` table
(`backend/app/models/controlled_vocabulary.py`), and ADR-029's template asked whether it could
serve as the tag vocabulary. On inspection it cannot: `controlled_vocabularies` is a **global,
platform-wide** table (no `organization_id` column) seeded with MINSEQE field values. Lab
document tags are **org-scoped and admin-managed per organization**, so reusing that table
would leak one org's tags into another.

## Decision

Use a flat document list with a controlled, org-scoped tag vocabulary instead of folders.

- A dedicated `lab_document_tags` table (org-scoped, `UNIQUE(organization_id, name)`) holds the
  vocabulary. It is **not** the global `controlled_vocabularies` table, for the org-scoping
  reason above.
- A `lab_document_tag_assignments` join table maps documents to tags (many-to-many).
- A default set is seeded on org creation: `manual`, `contact`, `procedure`, `policy`,
  `standard`. Seeding for new orgs is added to the org bootstrap path alongside
  `seed_builtin_roles`; seeding for existing orgs is an Alembic data migration that inserts the
  defaults for every organization, mirroring the per-org seed pattern in
  `alembic/versions/083_literature_library.py` (`INSERT ... SELECT o.id FROM organizations o ... WHERE NOT EXISTS`).
- Tag vocabulary management is gated by a dedicated `lab_document_tags:manage` permission
  (resource `lab_document_tags`, action `manage`), admin-default and grantable to custom roles.
- A tag cannot be deleted while any document references it; the API returns an error naming the
  blocking documents.

## Rationale

A dedicated org-scoped table is the right call because the vocabulary is per-organization and
admin-governed, which the global `controlled_vocabularies` table cannot express. Tags (vs.
folders) let a document belong to multiple logical categories at once and avoid hierarchy
disputes. A controlled (not free-text) vocabulary preserves filterability and prevents tag
sprawl. The join table mirrors established many-to-many patterns already used in the schema.

## Consequences

**Positive:**

- Documents can belong to multiple logical categories simultaneously.
- No disputes about folder hierarchy; filtering by tag is fast and intuitive.
- New organizational dimensions can be added without restructuring.
- Org-scoping is correct and isolated per tenant.

**Negative:**

- Tag discipline requires governance; the admin-controlled vocabulary addresses this.
- Users accustomed to folders may need adjustment.
- A second tag concept now exists in the platform (distinct from `files.tags_json` free-text
  tags and from `controlled_vocabularies`); UI copy should keep the Lab Knowledge tag scope clear.
