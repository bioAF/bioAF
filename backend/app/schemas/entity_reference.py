"""Links to a project or an experiment, as other records reference them: id and name only.

Session, work-node, experiment and pipeline-run responses each declared their own identical
copy. One definition keeps the wire shape identical everywhere and gives the internal OpenAPI
schema one component per contract. The project-detail experiment summary
(``app.schemas.project.ExperimentSummary``) carries more fields and is a separate contract.
"""

from pydantic import BaseModel


class ProjectSummary(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class ExperimentReference(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}
