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
from app.models.user import User
from app.services.samplesheet_declaration import parse_declaration


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


def _stamped_field(field: dict, user_id: int | None, previous: dict) -> dict:
    """One declared column, carrying who declared it.

    A column is a binding by another name, so it takes the same attribution
    design section 10 puts on every binding. An unchanged column keeps its
    original stamp: re-saving a declaration to add one column must not reassign
    authorship of the rest, or the record answers "who last pressed save".
    """
    name = str(field.get("name") or "").strip()
    held = previous.get(name)
    kept = {k: v for k, v in field.items() if k not in ("set_by_user_id", "set_at")}
    if held and {k: v for k, v in held.items() if k not in ("set_by_user_id", "set_at")} == kept:
        return held
    return {**kept, "set_by_user_id": user_id, "set_at": datetime.now(timezone.utc).isoformat()}


class SamplesheetMappingService:
    @staticmethod
    def declared_columns(mapping) -> list[dict]:
        """A mapping's declared columns, stripped of authorship the same way
        ``flatten`` strips it from values. Empty when nothing was declared,
        which every reader answers with today's generic sheet."""
        stored = (getattr(mapping, "columns_json", None) or {}) if mapping is not None else {}
        fields = stored.get("fields") if isinstance(stored, dict) else None
        if not isinstance(fields, list):
            return []
        return [
            {k: v for k, v in field.items() if k not in ("set_by_user_id", "set_at")}
            for field in fields
            if isinstance(field, dict) and field.get("name")
        ]

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
    async def describe(session: AsyncSession, snapshot: dict | None) -> dict | None:
        """A run's stored design with its authors named rather than numbered.

        The stamps hold a user id, which is the right key and the wrong thing to
        read: "who set this value" is a question about a person. A stamp whose
        user is gone keeps its value and loses only the name, because losing the
        record because somebody left the lab would be the worse failure.
        """
        if not snapshot:
            return None

        stamps: list[dict] = []
        for columns in (snapshot.get("values") or {}).values():
            if isinstance(columns, dict):
                stamps.extend(entry for entry in columns.values() if isinstance(entry, dict))
        stamps.extend(entry for entry in (snapshot.get("bindings") or {}).values() if isinstance(entry, dict))

        user_ids = {entry.get("set_by_user_id") for entry in stamps if entry.get("set_by_user_id")}
        names: dict[int, str] = {}
        if user_ids:
            rows = await session.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
            names = {uid: (name or email) for uid, name, email in rows.all()}

        def _described(entry: dict) -> dict:
            return {
                "value": str(entry.get("value", "")),
                "set_by": names.get(entry.get("set_by_user_id")),
                "set_at": entry.get("set_at"),
            }

        return {
            "values": {
                str(sample_id): {str(column): _described(entry) for column, entry in columns.items() if isinstance(entry, dict)}
                for sample_id, columns in (snapshot.get("values") or {}).items()
                if isinstance(columns, dict)
            },
            "bindings": {
                str(column): _described(entry)
                for column, entry in (snapshot.get("bindings") or {}).items()
                if isinstance(entry, dict)
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
        columns: list[dict] | None = None,
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
            # Named for what it is, and NOT `columns`: that is this method's own
            # parameter, and rebinding it here left every caller that stated a
            # per-sample value also declaring a sheet made of that value's
            # column names.
            for sample_id, stated in values.items():
                if not isinstance(stated, dict):
                    continue
                held = previous.get(str(sample_id)) or {}
                per_column = {
                    str(column): _stamp(str(value).strip(), user_id, held.get(str(column)))
                    for column, value in stated.items()
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

        if columns is not None:
            # Parsed before it is stored, and the parse RAISES on a binding
            # bioAF cannot resolve. Storing an unresolvable declaration would
            # leave a column permanently unanswerable and surface it at launch
            # time, which is the late failure this whole project removes.
            parse_declaration({"fields": columns})
            previous_columns = {
                str(field.get("name")): field
                for field in ((mapping.columns_json or {}).get("fields") or [])
                if isinstance(field, dict)
            }
            mapping.columns_json = {
                "fields": [_stamped_field(field, user_id, previous_columns) for field in columns]
            }

        mapping.updated_by_user_id = user_id
        await session.flush()
        return mapping
