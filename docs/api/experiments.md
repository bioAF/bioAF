# Experiments

An experiment is a specific run of work inside (or outside of) a project.
Samples are grouped under an experiment.

## Endpoints

| Method | Path | Scope |
| --- | --- | --- |
| `POST` | `/experiments` | `experiments:create` |
| `GET` | `/experiments` | `experiments:view` |
| `GET` | `/experiments/{id}` | `experiments:view` |
| `GET` | `/experiments/by-external/{external_id}` | `experiments:view` |
| `PATCH` | `/experiments/{id}` | `experiments:edit` |

## POST /experiments

Create a new experiment.

```json
{
  "external_id": "LIMS-EXP-042",
  "name": "RNA-seq batch 3",
  "project_id": 42,
  "project_external_id": "LIMS-PROJ-001",
  "hypothesis": "optional",
  "description": "optional",
  "expected_sample_count": 12,
  "variables_json": { "library_kit": "v3.1" },
  "custom_fields": [
    {"field_name": "sequencer", "field_value": "NovaSeq X"}
  ]
}
```

- `external_id` (required, 1-255). Duplicate within org -> 409.
- `name` (required, 1-255).
- `project_id` xor `project_external_id` (both optional; if both present
  `project_id` wins). When supplied, the project is validated to belong
  to the SA's org; otherwise -> 404 `project_not_found`.
- `status` is **not accepted**; creates are forced to `registered`.
  Including `status` -> 400 `status_writes_not_permitted`.

Returns `201 Created`:

```json
{
  "id": 7,
  "external_id": "LIMS-EXP-042",
  "name": "RNA-seq batch 3",
  "code": "bioae-0025",
  "project_id": 42,
  "status": "registered",
  "hypothesis": null,
  "description": null,
  "expected_sample_count": 12,
  "variables_json": {"library_kit": "v3.1"},
  "created_at": "2026-05-14T15:00:00Z",
  "custom_fields": [{"field_name": "sequencer", "field_value": "NovaSeq X"}]
}
```

`code` is auto-generated as `{org-prefix}e-{0000}` and cannot be set by
the caller.

## GET /experiments

List experiments in the calling SA's org. Query params:

- `project_id`, `status`, `external_id`, `q` (free-text), `limit`, `cursor`.

## GET /experiments/{id}, GET /experiments/by-external/{external_id}

Return a single `ExperimentOut`, or `404 experiment_not_found`.

## PATCH /experiments/{id}

Update mutable fields. All fields optional.

```json
{
  "name": "renamed",
  "hypothesis": "updated",
  "description": "updated",
  "expected_sample_count": 24,
  "variables_json": {"library_kit": "v3.2"},
  "custom_fields": [
    {"field_name": "sequencer", "field_value": "NovaSeq 6000"}
  ]
}
```

`status` is not accepted; including it returns 400.

Returns `200 OK` with the updated `ExperimentOut`.
