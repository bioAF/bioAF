"""plan_8_2 section 2.2: the paper's resources as one canonical, typed inventory, whoever reads it.

Study 44's report listed "GSM2454338 and GSM2454339" as a deposit in an unrecognised archive, "table S1"
beside "Supplemental Table S1", and three PDB structures that the resource table called structures and
the resource statements called an unknown archive. Two sentences read "bioAF has an archive bioAF does
not recognise" and "bioAF has no other adapter". Each surface had built its own identity.

This is the one pass the read, the report and the resource statements share. It is pure and idempotent,
so a plan recorded before it reads the same as one recorded after:

- a compound identifier ("A and B", "A, B") becomes one row per identifier it names, with the original
  kept as ``split_from``;
- a GEO sample (GSM) or other sample record stays a sample of its archive (``level: sample``), never an
  unrelated deposit and never an unrecognised archive; its series is kept when a row names it;
- a supplement citation ("table S1") joins the manifest row with the same citation, its label kept as a
  reference; a citation with no manifest row is still a journal supplement;
- an archive recognised in context (a PDB code after "PDB") keeps that archive;
- support is stated as separate facts: recognised, metadata verified, download supported, analysis
  supported, access. An unsupported acquisition never reads as absent data.
"""

from __future__ import annotations

import re

from app.services.archive_discovery import ARCHIVE_NAMES, OTHER, classify_archive, is_sample_accession

YES, NO, UNKNOWN = "yes", "no", "unknown"

# "A and B", "A, B", "A; B", "A & B": a compound identifier's separators.
_SEPARATOR = re.compile(r"\s*(?:,|;|&|\band\b)\s*", re.I)

_SAMPLE_ARCHIVES = {
    "GSM": "geo",
    "SRR": "sra",
    "ERR": "sra",
    "DRR": "sra",
    "SRS": "sra",
    "EGAF": "ega",
    "SAMN": "biosample",
    "SAMEA": "biosample",
}

# Where a resource can be opened, by archive.
_LINKS = {
    "geo": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={id}",
    "sra": "https://www.ncbi.nlm.nih.gov/sra/{id}",
    "ega": "https://ega-archive.org/studies/{id}",
    "arrayexpress": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{id}",
    "pride": "https://www.ebi.ac.uk/pride/archive/projects/{id}",
    "massive": "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={id}",
    "pdb": "https://www.rcsb.org/structure/{id}",
    "emdb": "https://www.ebi.ac.uk/emdb/{id}",
    "biosample": "https://www.ncbi.nlm.nih.gov/biosample/{id}",
}

# The archives bioAF can retrieve from (``resource_inventory``'s own list).
_RETRIEVABLE_ARCHIVES = ("geo", "sra", "github", "gitlab", "journal_supplement")

_TYPE_WORDS = {
    "proteomics_data": "proteomics data",
    "structure": "protein structures",
    "binding_assay": "binding assay data",
}


def _key(identifier: str) -> str:
    return " ".join(str(identifier or "").split()).upper()


def _citation_key(text: str) -> str | None:
    from app.services.supplement_inventory import _citations

    found = _citations(text or "")
    return found[0]["key"] if len(found) == 1 else None


def _parts(identifier: str) -> list[str]:
    """The identifiers a compound identifier names, or the identifier itself."""
    pieces = [p.strip() for p in _SEPARATOR.split(identifier or "") if p and p.strip()]
    if len(pieces) < 2:
        return [identifier]
    if all(is_sample_accession(p) or classify_archive(p) != OTHER for p in pieces):
        return pieces
    return [identifier]


def _sample_archive(identifier: str) -> str | None:
    match = re.match(r"^([A-Za-z]+)\d+$", identifier or "")
    return _SAMPLE_ARCHIVES.get(match.group(1).upper()) if match else None


def archive_phrase(archive: str | None, identifier: str | None = None) -> str:
    """How bioAF's own limitation with an archive is said: never "an archive bioAF does not recognise" as a
    thing bioAF has, never "no other adapter"."""
    if not archive or archive == OTHER:
        return (
            f"bioAF does not recognise the archive that holds {identifier}"
            if identifier
            else ("bioAF does not recognise the archive")
        )
    return f"bioAF has no {ARCHIVE_NAMES.get(archive, archive)} adapter"


def unsupported_lookup_reason(archive: str | None, identifier: str) -> str:
    """Why nothing about a deposit could be looked up, in the one set of words (plan_8_2 section 2.2)."""
    if not archive or archive == OTHER:
        return f"{archive_phrase(archive, identifier)}, so what {identifier} holds is unknown"
    return f"bioAF has no adapter for {ARCHIVE_NAMES.get(archive, archive)}, so what {identifier} holds is unknown"


def link_for(archive: str | None, identifier: str | None) -> str | None:
    identifier = str(identifier or "").strip()
    if not identifier:
        return None
    if identifier.lower().startswith(("http://", "https://")):
        return identifier
    if identifier.lower().startswith("10."):
        return f"https://doi.org/{identifier}"
    template = _LINKS.get(archive or "")
    return template.format(id=identifier) if template else None


def _limitation(row: dict) -> str | None:
    """bioAF's limitation with this resource, in the shared words."""
    archive = row.get("archive")
    identifier = row.get("identifier")
    if row.get("level") == "sample":
        name = ARCHIVE_NAMES.get(archive, archive or "an archive")
        series = row.get("series")
        held = f"series {series}" if series else "a series not established here"
        return f"{identifier} is a {name} sample record in {held}; bioAF reads deposits, not single samples"
    previous = (row.get("bioaf") or {}).get("limitation")
    type_words = _TYPE_WORDS.get(row.get("type"))
    if archive == "journal_supplement":
        return previous if previous and "does not recognise" not in previous else None
    if type_words:
        return f"{archive_phrase(archive, identifier)}, and no check reads {type_words}"
    if archive == OTHER or not archive:
        return archive_phrase(archive, identifier)
    if previous and ("does not recognise" in previous or "no other adapter" in previous):
        return archive_phrase(archive, identifier)
    return previous


def _support(row: dict, deposit: dict | None) -> dict:
    bioaf = row.get("bioaf") or {}
    archive = row.get("archive")
    if deposit is not None and deposit.get("looked_up") is False:
        verified = UNKNOWN
    elif deposit is not None and deposit.get("exists") in (YES, NO):
        verified = deposit["exists"]
    else:
        verified = UNKNOWN
    unreadable_type = row.get("type") in _TYPE_WORDS
    no_adapter = row.get("level") == "sample" or archive not in _RETRIEVABLE_ARCHIVES
    return {
        "recognized": NO if (not archive or archive == OTHER) else YES,
        "metadata_verified": verified,
        "download_supported": NO if no_adapter or unreadable_type else (bioaf.get("retrievable") or UNKNOWN),
        "analysis_supported": NO if unreadable_type else (bioaf.get("analyzable") or UNKNOWN),
        "access": (deposit or {}).get("access") or UNKNOWN,
    }


def canonical_resources(
    rows: list[dict] | None, *, supplements: list[dict] | None = None, deposits: list[dict] | None = None
) -> list[dict]:
    """The canonical inventory of ``rows`` (``build_resource_inventory``'s shape). Pure and idempotent."""
    deposits_by_key = {
        _key(d.get("accession")): d for d in deposits or [] if isinstance(d, dict) and d.get("accession")
    }
    expanded: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("identifier"):
            continue
        parts = _parts(str(row["identifier"]))
        for part in parts:
            item = {**row, "identifier": part}
            if len(parts) > 1:
                item["split_from"] = row["identifier"]
            item["references"] = list(dict.fromkeys([*(row.get("references") or []), part]))
            expanded.append(item)

    # Samples, context-recognised archives and supplement citations.
    for item in expanded:
        identifier = str(item["identifier"])
        sample_archive = _sample_archive(identifier) if is_sample_accession(identifier) else None
        if sample_archive:
            item["archive"] = sample_archive
            item["level"] = "sample"
        elif item.get("archive") in (None, OTHER):
            recognised = classify_archive(identifier)
            if recognised != OTHER:
                item["archive"] = recognised
            elif _citation_key(identifier):
                item["archive"] = "journal_supplement"
                item["type"] = "supplementary_file"
        item.setdefault("level", "deposit")

    # A supplement citation joins the manifest row with the same citation.
    merged: list[dict] = []
    by_citation: dict[str, dict] = {}
    for item in expanded:
        if item.get("archive") == "journal_supplement":
            keys = {k for k in (_citation_key(r) for r in [item["identifier"], *(item.get("references") or [])]) if k}
            target = next((by_citation[k] for k in keys if k in by_citation), None)
            if target is not None:
                _absorb(target, item)
                continue
            for key in keys:
                by_citation.setdefault(key, item)
        merged.append(item)
    # The manifest's own label leads, whichever row came first.
    for item in merged:
        if item.get("archive") == "journal_supplement":
            manifest = "supplement_manifest" in (item.get("found_by") or [])
            labels = [r for r in item.get("references") or [] if r]
            if manifest and labels:
                item["identifier"] = next(
                    (r for r in labels if r.lower().startswith(("supplement", "additional"))), item["identifier"]
                )

    seen: dict[str, dict] = {}
    result: list[dict] = []
    for item in merged:
        key = _key(item["identifier"])
        if key in seen:
            _absorb(seen[key], item)
            continue
        seen[key] = item
        result.append(item)
    for position, item in enumerate(result, start=1):
        item["id"] = item.get("id") if item.get("id") and not item.get("split_from") else f"r{position}"
        item["link"] = link_for(item.get("archive"), item["identifier"])
        deposit = deposits_by_key.get(_key(item["identifier"]))
        item["limitation"] = _limitation(item)
        item["bioaf"] = {**(item.get("bioaf") or {}), "limitation": item["limitation"]}
        item["support"] = _support(item, deposit)
    return result


def _absorb(target: dict, other: dict) -> None:
    """Fold a duplicate row into the one it names: references and discovery kept, gaps filled."""
    target["references"] = list(
        dict.fromkeys([*(target.get("references") or []), *(other.get("references") or []), other["identifier"]])
    )
    target["found_by"] = list(dict.fromkeys([*(target.get("found_by") or []), *(other.get("found_by") or [])]))
    for field in ("role", "stated_in", "linked_by"):
        if not target.get(field) and other.get(field):
            target[field] = other[field]
    ids = list(target.get("reported_experiment_ids") or [])
    ids.extend(e for e in other.get("reported_experiment_ids") or [] if e not in ids)
    target["reported_experiment_ids"] = ids
