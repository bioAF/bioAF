"""Saving and resolving the samplesheet answers a scientist has already given.

Re-stating a co-assembly grouping for every run of the same design is the cost
this removes. What it must not do is carry a design silently into a run it does
not fit, so two rules do the work:

**Most specific scope wins, and the caller is told which one it used.** Naming
the source is the load-bearing half. Without it an inherited organization-wide
binding looks identical to one somebody set for this experiment, and a wrong
inheritance stays invisible until the review step.

**Each value is stamped with who set it and when.** Whoever fills the design grid
is often not whoever launches: the wet-lab scientist knows the design, the
bioinformatician runs the pipeline. A launcher-only record names the wrong person
for the value that turned out wrong. A value that did not change keeps its
original author, so re-saving a mapping does not quietly reassign authorship of
everything in it.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.samplesheet_mapping import MAPPING_SCOPES, SamplesheetMapping


def _stamp(value: str, user_id: int | None, previous: dict | None) -> dict:
    """One value, with the authorship it should carry.

    An unchanged value keeps the stamp it already had. Re-saving a mapping to
    edit one cell must not reassign authorship of the other two hundred, or the
    record answers "who last pressed save" rather than "who set this value",
    which is the question an audited lab is actually asking.
    """
    if previous and previous.get("value") == value:
        return previous
    return {
        "value": value,
        "set_by_user_id": user_id,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }


class SamplesheetMappingService:
    @staticmethod
    def flatten(mapping) -> dict[str, dict[str, str]]:
        """A mapping's per-sample values in the shape generation consumes.

        The stored form carries authorship per value; the samplesheet needs only
        the values. Keyed by sample id as strings throughout, matching both the
        JSON that arrives from the browser and what the generator expects.
        """
        stored = (getattr(mapping, "values_json", None) or {}) if mapping is not None else {}
        flattened: dict[str, dict[str, str]] = {}
        for sample_id, columns in stored.items():
            if not isinstance(columns, dict):
                continue
            values = {
                str(column): str(entry.get("value", ""))
                for column, entry in columns.items()
                if isinstance(entry, dict) and entry.get("value")
            }
            if values:
                flattened[str(sample_id)] = values
        return flattened

    @staticmethod
    def flatten_bindings(mapping) -> dict[str, str]:
        """A mapping's bindings, stripped of authorship the same way."""
        stored = (getattr(mapping, "bindings_json", None) or {}) if mapping is not None else {}
        return {
            str(column): str(entry.get("value", ""))
            for column, entry in stored.items()
            if isinstance(entry, dict) and entry.get("value")
        }

    @staticmethod
    def snapshot(
        values: dict[str, dict[str, str]] | None,
        bindings: dict[str, str] | None,
        user_id: int | None,
    ) -> dict:
        """The design a run used, stamped and frozen onto that run.

        Taken at launch and never updated, because a mapping edited afterwards
        must not rewrite the history of a run that already used it. A run that
        stated nothing records empty rather than null: "nothing was stated" is a
        fact worth keeping, and a null could equally mean the record was not
        being written yet.
        """
        stamped_values: dict[str, dict] = {}
        for sample_id, columns in (values or {}).items():
            if not isinstance(columns, dict):
                continue
            per_column = {
                str(column): _stamp(str(value).strip(), user_id, None)
                for column, value in columns.items()
                if value is not None and str(value).strip()
            }
            if per_column:
                stamped_values[str(sample_id)] = per_column

        return {
            "values": stamped_values,
            "bindings": {
                str(column): _stamp(str(value).strip(), user_id, None)
                for column, value in (bindings or {}).items()
                if value is not None and str(value).strip()
            },
        }

    @staticmethod
    async def resolve(session: AsyncSession, org_id: int, pipeline_key: str, experiment_id: int | None):
        """The mapping to prefill this launch from, and the scope it came from.

        Walks experiment, then project, then organization, and stops at the first
        match, so an organization-wide binding never overrides one somebody set
        for this experiment. Returns ``(None, None)`` when nothing applies.

        Most-recently-edited was rejected as the rule: it makes resolution
        unpredictable and lets an organization-level edit override every
        experiment's own configuration.
        """
        project_id = None
        if experiment_id is not None:
            project_id = await session.scalar(select(Experiment.project_id).where(Experiment.id == experiment_id))

        for scope in MAPPING_SCOPES:
            query = select(SamplesheetMapping).where(
                SamplesheetMapping.organization_id == org_id,
                SamplesheetMapping.pipeline_key == pipeline_key,
                SamplesheetMapping.scope == scope,
            )
            if scope == "experiment":
                if experiment_id is None:
                    continue
                query = query.where(SamplesheetMapping.experiment_id == experiment_id)
            elif scope == "project":
                if project_id is None:
                    continue
                query = query.where(SamplesheetMapping.project_id == project_id)

            found = (await session.execute(query)).scalars().first()
            if found is not None:
                return found, scope
        return None, None

    @staticmethod
    async def save(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        pipeline_key: str,
        scope: str,
        *,
        experiment_id: int | None = None,
        project_id: int | None = None,
        values: dict[str, dict[str, str]] | None = None,
        bindings: dict[str, str] | None = None,
    ) -> SamplesheetMapping:
        """Create or update THE mapping for this pipeline at this scope.

        One per pipeline per scope, so a second save at the same scope edits the
        first rather than adding a rival. Comparative work runs twice instead,
        and each run keeps its own snapshot.

        Values arrive plain and are stored stamped. A blank value removes the
        entry rather than recording an empty answer, because a blank cell is an
        unanswered question and a required column with no answer must go on
        blocking the launch.
        """
        if scope not in MAPPING_SCOPES:
            raise ValueError(f"Unknown mapping scope {scope!r}")

        query = select(SamplesheetMapping).where(
            SamplesheetMapping.organization_id == org_id,
            SamplesheetMapping.pipeline_key == pipeline_key,
            SamplesheetMapping.scope == scope,
        )
        if scope == "experiment":
            query = query.where(SamplesheetMapping.experiment_id == experiment_id)
        elif scope == "project":
            query = query.where(SamplesheetMapping.project_id == project_id)

        mapping = (await session.execute(query)).scalars().first()
        if mapping is None:
            mapping = SamplesheetMapping(
                organization_id=org_id,
                pipeline_key=pipeline_key,
                scope=scope,
                experiment_id=experiment_id if scope == "experiment" else None,
                project_id=project_id if scope == "project" else None,
                created_by_user_id=user_id,
            )
            session.add(mapping)

        if values is not None:
            previous = mapping.values_json or {}
            stamped: dict[str, dict] = {}
            for sample_id, columns in values.items():
                if not isinstance(columns, dict):
                    continue
                held = previous.get(str(sample_id)) or {}
                per_column = {
                    str(column): _stamp(str(value).strip(), user_id, held.get(str(column)))
                    for column, value in columns.items()
                    if value is not None and str(value).strip()
                }
                if per_column:
                    stamped[str(sample_id)] = per_column
            mapping.values_json = stamped

        if bindings is not None:
            previous_bindings = mapping.bindings_json or {}
            mapping.bindings_json = {
                str(column): _stamp(str(value).strip(), user_id, previous_bindings.get(str(column)))
                for column, value in bindings.items()
                if value is not None and str(value).strip()
            }

        mapping.updated_by_user_id = user_id
        await session.flush()
        return mapping
