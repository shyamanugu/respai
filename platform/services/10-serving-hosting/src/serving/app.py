"""Thin FastAPI wrapper around Orchestration's Pipeline engine — translates
HTTP in/out only, contains no pipeline logic itself (see point 1 of
Orchestration's "Future Deployment Path" in `08-orchestration/README.md`). A
usecase wires its own pipelines into the `PipelineRegistry` this app is
constructed with; nothing in this module is usecase-specific.
"""
from fastapi import FastAPI, HTTPException
from orchestration.state import State

from .pipeline_registry import PipelineRegistry
from .schemas import RunPipelineRequest, RunPipelineResponse


def create_app(registry: PipelineRegistry) -> FastAPI:
    app = FastAPI(title="LLMOps Pipeline Server")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/pipelines/{name}/run", response_model=RunPipelineResponse)
    def run_pipeline(name: str, request: RunPipelineRequest):
        try:
            pipeline = registry.get(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        state = State()
        for key, value in request.values.items():
            state.set(key, value)

        result = pipeline.run(state, environment=request.environment)
        return RunPipelineResponse(session_id=result.session_id, values=result.values)

    return app
