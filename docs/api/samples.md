# Samples

A sample is a physical specimen registered against an experiment. QC and
status moves are bioAF-managed; the public API only accepts metadata.

## Endpoints

| Method | Path | Scope |
| --- | --- | --- |
| `POST` | `/samples` | `samples:create` |
| `GET` | `/samples` | `samples:view` |
| `GET` | `/samples/{id}` | `samples:view` |
| `GET` | `/samples/by-external/{external_id}?experiment_id=...` | `samples:view` |
| `PATCH` | `/samples/{id}` | `samples:edit` |

## POST /samples

Create a new sample under an experiment.

```json
{
  "external_id": "LIMS-SMP-1001",
  "experiment_id": 7,
  "organism": "Mus musculus",
  "tissue_type": "cortex",
  "donor_source": "C57BL/6J",
  "treatment_condition": "control",
  "chemistry_version": "v3.1",
  "cell_count": 10000,
  "prep_notes": "free text",
  "molecule_type": "total RNA",
  "library_prep_method": "10x Chromium",
  "custom_fields": [{"field_name": "donor_age", "field_value": "8wk"}]
}
```

- `external_id` (required, 1-255). Duplicate within the same `experiment_id`
  -> 409 `external_id_already_exists`.
- `experiment_id` (required). Must belong to the SA's org or 404
  `experiment_not_found`.
- `qc_status` and `status` are not accepted; including them returns 400.

Returns `201 Created`:

```json
{
  "id": 901,
  "external_id": "LIMS-SMP-1001",
  "experiment_id": 7,
  "organism": "Mus musculus",
  "tissue_type": "cortex",
  "donor_source": "C57BL/6J",
  "treatment_condition": "control",
  "chemistry_version": "v3.1",
  "cell_count": 10000,
  "prep_notes": "free text",
  "molecule_type": "total RNA",
  "library_prep_method": "10x Chromium",
  "qc_status": null,
  "status": "registered",
  "created_at": "2026-05-14T15:00:00Z",
  "custom_fields": [{"field_name": "donor_age", "field_value": "8wk"}]
}
```

## GET /samples

List samples in the calling SA's org. Query params:

- `experiment_id` (optional).
- `external_id` (optional, exact match within scope).
- `q` (optional substring match against `external_id` or `organism`).
- `limit`, `cursor`.

## GET /samples/{id}

Return a single sample or `404 sample_not_found`.

## GET /samples/by-external/{external_id}

Sample external_id is unique only within an experiment, so this endpoint
requires `experiment_id` as a query parameter:

```http
GET /samples/by-external/LIMS-SMP-1001?experiment_id=7
```

## PATCH /samples/{id}

Update mutable fields. All fields optional.

```json
{
  "organism": "Mus musculus",
  "tissue_type": "cortex",
  "donor_source": "C57BL/6J",
  "treatment_condition": "treatment-A",
  "chemistry_version": "v3.1",
  "cell_count": 11000,
  "prep_notes": "re-prepped on 2026-05-14",
  "molecule_type": "total RNA",
  "library_prep_method": "10x Chromium",
  "custom_fields": [{"field_name": "donor_age", "field_value": "9wk"}]
}
```

`qc_status` and `status` are not accepted on this surface.

Returns `200 OK` with the updated `SampleOut`.
