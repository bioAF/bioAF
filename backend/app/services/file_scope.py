"""Which catalogue files belong to an experiment or a project.

One definition shared by the file listing, data-export estimates and export builders, so a file a
scientist sees under an experiment is the file its export carries. Callers keep their own paging,
search and FASTQ rules; this owns only membership and scope.

- ``live_files(org_id)``: the organization's files that have not been deleted from view.
- ``experiment_files(experiment_id, org_id)``: attached to the experiment directly, or linked to one
  of its samples.
- ``project_files(project_id, org_id)``: attached to the project, to one of its experiments, or
  linked to a sample of one of its experiments.

Related records are scoped to the organization too: an experiment of another organization never
contributes members, even when an identifier is reused across them.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_, select

from app.models.experiment import Experiment
from app.models.file import File
from app.models.sample import Sample, sample_files


def live_files(org_id: int) -> ColumnElement[bool]:
    """The organization's files that are not deleted from view."""
    return and_(File.organization_id == org_id, File.deleted_at.is_(None))


def _experiments_in_org(org_id: int):
    return select(Experiment.id).where(Experiment.organization_id == org_id)


def experiment_files(experiment_id: int, org_id: int) -> ColumnElement[bool]:
    """Attached to the experiment directly, or linked to a sample that belongs to it."""
    samples = select(Sample.id).where(
        Sample.experiment_id == experiment_id, Sample.experiment_id.in_(_experiments_in_org(org_id))
    )
    via_samples = select(sample_files.c.file_id).where(sample_files.c.sample_id.in_(samples))
    return or_(File.experiment_id == experiment_id, File.id.in_(via_samples))


def project_experiments(project_id: int, org_id: int):
    """The ids of the project's experiments, as a subquery."""
    return select(Experiment.id).where(Experiment.project_id == project_id, Experiment.organization_id == org_id)


def project_files(project_id: int, org_id: int) -> ColumnElement[bool]:
    """Attached to the project, to one of its experiments, or linked to a sample of one."""
    experiments = project_experiments(project_id, org_id)
    samples = select(Sample.id).where(Sample.experiment_id.in_(experiments))
    via_samples = select(sample_files.c.file_id).where(sample_files.c.sample_id.in_(samples))
    return or_(File.project_id == project_id, File.experiment_id.in_(experiments), File.id.in_(via_samples))
