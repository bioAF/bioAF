"""Connection command response schema for SSH access."""

from typing import Literal

from pydantic import BaseModel


class ConnectionCommandResponse(BaseModel):
    command: str
    setup_guide: str
    warning: str
    target_type: Literal["pipeline_job", "notebook_session"]
    # The recorded provider workload (pipeline Job or notebook Pod).
    target_id: str
    namespace: str | None = None
    # The verified target the command opens a shell in, and the kubectl context it selects.
    pod_name: str
    container: str
    context: str
