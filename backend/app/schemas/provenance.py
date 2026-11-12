from typing import Literal

from pydantic import BaseModel


class ProvenanceNode(BaseModel):
    id: str
    type: Literal["experiment", "sample", "pipeline_run", "snapshot", "reference", "file", "project"]
    label: str
    metadata: dict = {}

    model_config = {"json_schema_serialization_defaults_required": True}


class ProvenanceEdge(BaseModel):
    source: str
    target: str
    relationship: str


class ProvenanceDAG(BaseModel):
    nodes: list[ProvenanceNode]
    edges: list[ProvenanceEdge]
