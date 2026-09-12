"""change_7.5 section 2.1: a typed inventory of every resource the paper names, linked to its experiments.

Study 38 extracted `GSE144396, PXD016781`; study 37, the same paper, extracted three PDB structures too
and kept none of them. PRIDE read as "an archive bioAF does not recognise", and the RNA-seq deposit's
result tables were never listed because the deposit was never listed.

**Discovery is separate from bioAF's ability to use a resource.** ``bioaf.retrievable`` says whether an
adapter exists and access allows it; ``bioaf.analyzable`` says whether some check can use the content.
Neither decides whether a resource is listed: an unsupported archive stays visible with its role and
its limitation ("PXD016781, PRIDE, proteomics: bioAF has no PRIDE adapter").

**Type and role.** The identifier decides the type wherever it can: a PXD accession is proteomics data
whatever a model says. The model decides type and role only where the identifier does not (a Zenodo
DOI can be code or data). A role is the paper's own words, with where the paper says it.

**Links to reported experiments** come from the paper's own statements (an experiment names the
accessions its data are under) and, for a deposit the paper did not place, from repository metadata
(the library strategy its samples declare). An unlinked resource stays listed, marked unlinked.
"""

from __future__ import annotations

from app.services.archive_discovery import ARCHIVE_NAMES, OTHER, classify_archive, is_sample_accession

SEQUENCING_DATA = "sequencing_data"
PROTEOMICS_DATA = "proteomics_data"
STRUCTURE = "structure"
BINDING_ASSAY = "binding_assay"
CODE = "code"
SUPPLEMENTARY_FILE = "supplementary_file"
OTHER_DATA = "other_data"
RESOURCE_TYPES = (SEQUENCING_DATA, PROTEOMICS_DATA, STRUCTURE, BINDING_ASSAY, CODE, SUPPLEMENTARY_FILE, OTHER_DATA)

YES, NO, UNKNOWN = "yes", "no", "unknown"

# The type an identifier's archive settles on its own. Zenodo and figshare hold anything, so the model's
# reading decides those.
_TYPE_BY_ARCHIVE = {
    "geo": SEQUENCING_DATA,
    "sra": SEQUENCING_DATA,
    "ena": SEQUENCING_DATA,
    "ega": SEQUENCING_DATA,
    "arrayexpress": SEQUENCING_DATA,
    "pride": PROTEOMICS_DATA,
    "massive": PROTEOMICS_DATA,
    "pdb": STRUCTURE,
    "emdb": STRUCTURE,
    "github": CODE,
    "gitlab": CODE,
    "journal_supplement": SUPPLEMENTARY_FILE,
}

# Archives bioAF can retrieve from, and the content types some check can analyze.
_RETRIEVABLE_ARCHIVES = ("geo", "sra", "github", "gitlab", "journal_supplement")
_ANALYZABLE_TYPES = (SEQUENCING_DATA, CODE)
_ANALYZABLE_SUPPLEMENT_ROLES = ("results_table", "sample_metadata", "expression_matrix")


def _key(identifier: str) -> str:
    return " ".join(str(identifier or "").split()).upper()


def _new(identifier: str, archive: str) -> dict:
    return {
        "id": None,
        "type": None,
        "identifier": identifier,
        "archive": archive,
        "role": None,
        "stated_in": None,
        "found_by": [],
        "reported_experiment_ids": [],
        "linked_by": None,
        "looked_up": None,
        "listing": None,
        "bioaf": {"retrievable": UNKNOWN, "analyzable": UNKNOWN, "limitation": None},
    }


def _add_found(row: dict, how: str) -> None:
    if how not in row["found_by"]:
        row["found_by"].append(how)


def build_resource_inventory(
    *,
    scanned: list[dict] | None,
    model_resources: list[dict] | None,
    extracted_accessions: list[str] | None,
    supplements: list[dict] | None,
    deposits: list[dict] | None,
    experiments: list[dict] | None,
) -> list[dict]:
    """Every resource the paper names or its repositories link, typed and linked. Never raises."""
    rows: dict[str, dict] = {}

    def _row(identifier: str, archive: str | None = None) -> dict | None:
        identifier = " ".join(str(identifier or "").split())
        if not identifier or is_sample_accession(identifier):
            return None
        key = _key(identifier)
        if key not in rows:
            rows[key] = _new(identifier, archive or classify_archive(identifier))
        elif archive and rows[key]["archive"] == OTHER:
            rows[key]["archive"] = archive
        return rows[key]

    for item in scanned or []:
        row = _row(item.get("identifier"), item.get("archive"))
        if row is not None:
            _add_found(row, "text_scan")
    for accession in extracted_accessions or []:
        row = _row(accession)
        if row is not None:
            _add_found(row, "model")
    for item in model_resources or []:
        if not isinstance(item, dict):
            continue
        row = _row(item.get("identifier"))
        if row is None:
            continue
        _add_found(row, "model")
        row["role"] = row["role"] or _text(item.get("role"))
        row["stated_in"] = row["stated_in"] or _text(item.get("stated_in"))
        stated_type = _text(item.get("type"))
        if stated_type in RESOURCE_TYPES:
            row["model_type"] = stated_type
    for supplement in supplements or []:
        if not isinstance(supplement, dict) or supplement.get("kind") in ("figure", "index"):
            continue
        label = supplement.get("label") or supplement.get("filename")
        row = _row(label, "journal_supplement")
        if row is None:
            continue
        row["archive"] = "journal_supplement"
        _add_found(row, "supplement_manifest")
        row["supplement"] = {
            "filename": supplement.get("filename"),
            "role": supplement.get("role"),
            "resolved": bool(supplement.get("resolved")),
        }
    deposits_by_key = {_key(d.get("accession")): d for d in deposits or [] if isinstance(d, dict) and d.get("accession")}
    for key, deposit in deposits_by_key.items():
        row = rows.get(key) or _row(deposit.get("accession"), deposit.get("archive"))
        if row is not None and not row["found_by"]:
            _add_found(row, "repository_link")

    ordered = list(rows.values())
    for position, row in enumerate(ordered, start=1):
        row["id"] = f"r{position}"
        row["type"] = _TYPE_BY_ARCHIVE.get(row["archive"]) or row.pop("model_type", None) or OTHER_DATA
        row.pop("model_type", None)
        _describe(row, deposits_by_key.get(_key(row["identifier"])))
    _link(ordered, experiments or [], deposits_by_key)
    return ordered


def _text(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _describe(row: dict, deposit: dict | None) -> None:
    """What bioAF can do with a resource, kept apart from the fact that the paper names it."""
    archive = row["archive"]
    name = ARCHIVE_NAMES.get(archive, archive)
    bioaf = row["bioaf"]
    if deposit is not None:
        row["looked_up"] = deposit.get("looked_up") is not False
        listing = dict(deposit.get("listing") or {})
        listing["result_tables"] = list(deposit.get("result_tables") or [])
        listing["samples"] = deposit.get("registered_samples")
        row["listing"] = listing
        if deposit.get("looked_up") is False:
            bioaf.update(retrievable=UNKNOWN, analyzable=UNKNOWN, limitation="Not looked up")
            return
    if row["type"] in (PROTEOMICS_DATA, STRUCTURE, BINDING_ASSAY):
        bioaf.update(
            retrievable=NO,
            analyzable=NO,
            limitation=f"bioAF has no {name} adapter, and no check reads {row['type'].replace('_', ' ')}",
        )
        return
    if archive == "journal_supplement":
        supplement = row.get("supplement") or {}
        bioaf["retrievable"] = YES if supplement.get("resolved") else UNKNOWN
        role = supplement.get("role")
        bioaf["analyzable"] = (
            YES if role in _ANALYZABLE_SUPPLEMENT_ROLES else (UNKNOWN if not supplement.get("resolved") else NO)
        )
        return
    if archive not in _RETRIEVABLE_ARCHIVES:
        reason = "an archive bioAF does not recognise" if archive == OTHER else f"no {name} adapter"
        bioaf.update(retrievable=NO, analyzable=UNKNOWN, limitation=f"bioAF has {reason}")
        if archive == "ega":
            bioaf["limitation"] = "bioAF has no EGA adapter, and EGA data are under controlled access"
        return
    if deposit is not None and deposit.get("access") == "controlled":
        bioaf.update(retrievable=NO, limitation=f"{row['identifier']} is under controlled access")
    elif deposit is not None and deposit.get("exists") == NO:
        bioaf.update(retrievable=NO, limitation=f"{name} holds no record of {row['identifier']}")
    elif deposit is None or deposit.get("exists") == UNKNOWN:
        bioaf["retrievable"] = UNKNOWN if row["type"] != CODE else YES
    else:
        bioaf["retrievable"] = YES
    if row["type"] in _ANALYZABLE_TYPES:
        if deposit is not None and YES in (deposit.get("raw_data"), deposit.get("preprocessed_data")):
            bioaf["analyzable"] = YES
        elif deposit is not None and (row["listing"] or {}).get("result_tables"):
            bioaf["analyzable"] = YES
        elif row["type"] == CODE:
            bioaf["analyzable"] = YES
    if bioaf["limitation"] is None and deposit is not None and deposit.get("failure_reason"):
        bioaf["limitation"] = deposit.get("failure_reason")


def _link(rows: list[dict], experiments: list[dict], deposits_by_key: dict[str, dict]) -> None:
    """Link each resource to the reported experiments it holds data for."""
    from app.services.pipeline_mapper import route_for_library_strategy, same_route_family

    for experiment in experiments:
        named = {_key(r) for r in experiment.get("resources") or []}
        for row in rows:
            if _key(row["identifier"]) in named and experiment.get("id") not in row["reported_experiment_ids"]:
                row["reported_experiment_ids"].append(experiment["id"])
                row["linked_by"] = "paper"
    for row in rows:
        if row["reported_experiment_ids"]:
            continue
        deposit = deposits_by_key.get(_key(row["identifier"])) or {}
        strategies = (deposit.get("listing") or {}).get("library_strategies") or []
        families = [route_for_library_strategy(s) for s in strategies]
        pipelines = [p for route in families if route is not None for p in route.compatible]
        linked = [
            e["id"]
            for e in experiments
            if e.get("workflow") and any(same_route_family(e["workflow"], p) for p in pipelines)
        ]
        if linked:
            row["reported_experiment_ids"] = linked
            row["linked_by"] = "repository"
