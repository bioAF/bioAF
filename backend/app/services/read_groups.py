"""The read group: the level between a sample and its files.

bioAF's model was sample -> files with nothing in between, so a sample sequenced
over several lanes, or re-sequenced in a top-up, had nowhere to record which unit
a file came from. Three parts of the code improvised around that absence, and
four pipelines in the catalog encode the same axis under four different names:
sarek's ``lane``, mag's ``run``, taxprofiler's ``run_accession``, ampliseq's
``run``.

The concept is standard and it already has a name. In the SAM spec a **read
group** (``@RG``) is one unit of sequencing of one library, and its ``PU`` is
flowcell.lane.barcode. Every aligner and GATK's best practices use the term, so a
bioinformatician reads it correctly with no explanation, and it cannot be
confused with a Sample Batch or a Sequencing Batch, both of which are cohorts
ACROSS samples rather than a decomposition of one.

**Derived, not stored.** A ``read_groups`` table waits until something needs to
hang metadata on the unit itself; when it arrives it should carry a nullable
``library_id`` so a Library level can slot in above without re-parenting files
twice. Until then the grouping is computed from the typed columns migration 119
added, which is the same axis the sheet builder already groups rows by.

**Everything is optional and everything unknown collapses to one group.** A lab
receiving pre-merged FASTQs from a CRO, or pulling from a public archive, has no
lane at all and is wholly unaffected.
"""

from app.services.sample_sheet_service import _get_lane, _get_read_type, _typed

# What bioAF says when it holds no sequencing identity for a group at all. Not
# "unknown.0" and never a fabricated lane: the scientist reading this has one
# group because bioAF has exactly one fact, that these files came from somewhere.
UNRECORDED_LABEL = "Not recorded"


def _label(flowcell: str | None, lane: int | None, accession: str | None) -> str:
    """How a read group names itself, following ``PU`` as far as bioAF can.

    ``PU`` is flowcell.lane.barcode. bioAF holds the first two and deliberately
    refuses the third, because a single read's index can be a no-call and the
    barcode is a property of the demultiplexing, so the label carries only what
    is actually known.
    """
    if flowcell and lane:
        return f"{flowcell}.{lane}"
    if flowcell:
        return flowcell
    if accession:
        return accession
    if lane:
        return f"L{lane:03d}"
    return UNRECORDED_LABEL


def read_groups_for(files: list) -> list[dict]:
    """One entry per read group, in a deterministic order.

    Files a scientist has deleted belong to no group: deletion retires a file
    from every working view, and this is one of them. Its record survives, which
    is a different question.
    """
    grouped: dict[tuple, dict] = {}
    for f in files or []:
        if getattr(f, "deleted_at", None) is not None:
            continue
        flowcell = _typed(f, "flowcell_id", str) or None
        lane = _get_lane(f)
        accession = _typed(f, "source_run_accession", str) or None
        key = (flowcell or "", lane or 0, accession or "")
        group = grouped.setdefault(
            key,
            {
                "flowcell_id": flowcell,
                "lane": lane,
                "source_run_accession": accession,
                "label": _label(flowcell, lane, accession),
                "read_types": [],
                "files": [],
            },
        )
        group["files"].append({"id": getattr(f, "id", None), "filename": getattr(f, "filename", None)})
        read_type = _get_read_type(f)
        if read_type and read_type not in group["read_types"]:
            group["read_types"].append(read_type)

    for group in grouped.values():
        group["read_types"].sort()
    return [grouped[key] for key in sorted(grouped)]
