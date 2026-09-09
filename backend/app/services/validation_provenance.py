"""change_7.2 section 7: record what BUILD produced each stage of a study.

Studies 32 and 33 both reported version `2026.9.1`. The backend image had been rebuilt between them
and the version tag is mutable, so the two exports establish that observed behaviour differed and
cannot attribute the difference to code. That is what made a run-to-run comparison worthless.

**Per stage, not per study.** A study can span deployments: study 29 has been alive across at least
one rebuild.

**Provenance must be able to say "unknown".** Stages that ran before this existed cannot have their
build established, and stamping them with the current build would manufacture exactly the false
certainty this exists to remove. This is the same discipline the feature already applies to a lookup
that never happened.
"""

from __future__ import annotations

from datetime import datetime, timezone

UNKNOWN = "unknown"


def current_build() -> dict:
    """The build running right now, with an explicit unknown for anything not stamped in."""
    from app.config import settings

    return {
        "commit": (getattr(settings, "build_commit", "") or "").strip() or UNKNOWN,
        "image_digest": (getattr(settings, "build_image_digest", "") or "").strip() or UNKNOWN,
        "version": settings.app_version,
    }


def stages_of(study) -> list[dict]:
    return list((study.evidence_json or {}).get("stage_provenance") or [])


def record_stage(study, stage: str) -> dict | None:
    """Stamp the build for a stage the study is entering, once.

    Returns the record written, or None when this stage was already stamped by this build. Ticking
    every 30 seconds must not write a row every 30 seconds, and a stage that genuinely spans a
    redeploy gets a second row because the build changed, which is the fact worth keeping.
    """
    build = current_build()
    stages = stages_of(study)
    if stages:
        last = stages[-1]
        if last.get("stage") == stage and last.get("commit") == build["commit"]:
            return None

    record = {"stage": stage, "at": datetime.now(timezone.utc).isoformat(), **build}
    evidence = dict(study.evidence_json or {})
    evidence["stage_provenance"] = [*stages, record]
    study.evidence_json = evidence
    return record


def provenance_summary(study) -> dict:
    """What the report says about which builds produced this study.

    ``stages_before_recording`` is the honest statement for a study that started before this existed:
    it passed through stages whose build cannot be established, and no row may be invented for them.
    """
    stages = stages_of(study)
    return {
        "stages": stages,
        "builds": sorted({s.get("commit") or UNKNOWN for s in stages}),
        "spans_more_than_one_build": len({s.get("commit") for s in stages}) > 1,
        "stages_before_recording": not stages,
    }
