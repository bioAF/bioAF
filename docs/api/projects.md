# Projects

A project groups experiments and samples for a single line of work.

## Endpoints

| Method | Path | Scope |
| --- | --- | --- |
| `POST` | `/projects` | `projects:create` |
| `GET` | `/projects` | `projects:view` |
| `GET` | `/projects/{id}` | `projects:view` |
| `GET` | `/projects/by-external/{external_id}` | `projects:view` |
| `PATCH` | `/projects/{id}` | `projects:edit` |

## POST /projects

Create a new project.

```json
{
  "external_id": "LIMS-PROJ-001",
  "name": "scRNA-seq mouse cortex",
  "description": "optional",
  "hypothesis": "optional",
  "custom_fields": [
    {"field_name": "cohort", "field_value": "A"}
  ]
}
```

- `external_id` (required, 1-255 chars). Duplicate within org -> 409.
- `name` (required, 1-255 chars).
- `description`, `hypothesis` (optional, free text).
- `custom_fields` (optional). Delta-applied: new fields are inserted,
  matching `field_name` is updated, setting `field_value: null` deletes.

Returns `201 Created`:

```json
{
  "id": 42,
  "external_id": "LIMS-PROJ-001",
  "name": "scRNA-seq mouse cortex",
  "code": "bioap-0008",
  "description": null,
  "hypothesis": null,
  "status": "active",
  "created_at": "2026-05-14T15:00:00Z",
  "custom_fields": [{"field_name": "cohort", "field_value": "A"}]
}
```

`code` is auto-generated as `{org-prefix}p-{0000}` and cannot be set by
the caller.

## GET /projects

List projects in the calling SA's org. Query params:

- `status` (optional): filter by status (e.g. `active`).
- `external_id` (optional): exact match.
- `q` (optional): substring match against `name` or `code`.
- `limit` (default 50, max 200).
- `cursor` (optional): from a previous response's `next_cursor`.

Response:

```json
{
  "items": [ <ProjectOut>, ... ],
  "next_cursor": "12345"
}
```

## GET /projects/{id}

Returns a single `ProjectOut` or `404 project_not_found` (also if the
project belongs to another org).

## GET /projects/by-external/{external_id}

Lookup by the LIMS-side identifier. `404 project_not_found` if no match.

## PATCH /projects/{id}

Update mutable fields. Body fields are all optional; only fields present
in the request are written.

```json
{
  "name": "renamed",
  "description": "updated",
  "hypothesis": "updated",
  "custom_fields": [
    {"field_name": "cohort", "field_value": "B"},
    {"field_name": "color", "field_value": null}
  ]
}
```

`status` is not accepted on this surface.

Returns `200 OK` with the full updated `ProjectOut`.
