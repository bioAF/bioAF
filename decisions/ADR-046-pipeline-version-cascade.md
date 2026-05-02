# ADR-046: Pipeline Version Cascade via Event Bus

**Status:** Accepted
**Date:** 2026-04-26
**Deciders:** Brent (repository owner)

---

## Context

Custom pipeline versions reference a specific `EnvironmentVersion`. When a user rebuilds a pipeline environment (e.g., adds a new Conda package), all pipelines using that environment should automatically receive a new version pointing to the updated image. This maintains provenance: the version record captures exactly which image was used.

The naive approach -- having `environment_build_service` directly call into `CustomPipelineService` -- creates a coupling between the environment system and the pipeline system. The build service should not need to know about custom pipelines.

---

## Decision

### Event-Driven Cascade

1. **New event type:** `ENVIRONMENT_BUILD_COMPLETED` added to `event_types.py`. Emitted when any environment version build transitions to `"ready"`.

2. **Emission:** `environment_build_service.py` emits this event via the existing `event_bus` after a successful build, with payload: `environment_id`, `environment_version_id`.

3. **Subscription:** `CustomPipelineService` subscribes to `ENVIRONMENT_BUILD_COMPLETED` at application startup (same pattern as `NotificationRouter` subscribing to pipeline events).

4. **Cascade handler:** `handle_environment_build_completed(event)`:
   - Query: for each `CustomPipeline` that has any active version where `environment_version_id` belongs to the event's `environment_id`, find the highest-numbered active version.
   - For each such version, create a new `CustomPipelineVersion` with:
     - Incremented `version_number`
     - `version_trigger = "environment_cascade"`
     - Same code source, entrypoint, resources, log file path
     - Copied `CustomPipelineVariable` records
     - Updated `environment_version_id` pointing to the new build
   - The previous version remains `"active"` (users can still select it at launch).

### Variables

Custom pipeline variables follow the `ExperimentCustomField` / `SampleCustomField` pattern:

- **`custom_pipeline_variables` table:** `variable_name`, `default_value`, `variable_type` ("string", "number", "boolean"), `is_required`. Linked to `custom_pipeline_version_id`.
- **Definition:** Set when creating/editing a pipeline version. UI: grid with add/remove (same as experiment custom fields).
- **Values at launch:** Launch dialog renders a form from the version's variable definitions. Defaults pre-filled, required fields marked. Values stored in `PipelineRun.parameters_json`.
- **Delivery to script:** Environment variables (`PARAM_{NAME}` uppercased) and `/data/params.json` (JSON object). Both written by init container.
- **Cascade:** When creating a cascade version, variables are copied from the source version.

### Version Trigger Field

`CustomPipelineVersion.version_trigger` distinguishes why a version was created:

- `"user"` -- a person created or edited the version.
- `"environment_cascade"` -- the system created it in response to an environment rebuild.

The frontend uses this field plus `environment_version_id` comparison to label each version in the selection modal as "config change," "image change," or "both."

---

## Consequences

- The environment build service is decoupled from the pipeline system. It emits a generic event; subscribers decide what to do.
- The event bus pattern is already used for `PIPELINE_COMPLETED`, `PIPELINE_OOM`, etc. This is consistent.
- Version history may grow with cascade entries. The `version_trigger` field and UI labeling make this transparent.
- Variables are simple, typed, and follow a proven in-app pattern. They do not support complex structures (arrays, objects), which is intentional for v1.
