"""HTTP request/response shapes for the generic pipeline-run endpoint."""
from typing import Any

from pydantic import BaseModel


class RunPipelineRequest(BaseModel):
    values: dict[str, Any] = {}
    environment: str = "dev"


class RunPipelineResponse(BaseModel):
    session_id: str
    values: dict[str, Any]
