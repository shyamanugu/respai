"""Reference entrypoint — copy and adapt this in a real usecase's own repo,
registering that usecase's actual pipelines instead of the empty registry
below. This module exists so `uvicorn serving.main:app` has something to
point at for local smoke-testing the generic wrapper itself; it registers
no real pipeline, so every `/pipelines/{name}/run` call 404s until a real
usecase's entrypoint replaces this file. `/healthz` works as-is.
"""
from .app import create_app
from .pipeline_registry import PipelineRegistry

app = create_app(PipelineRegistry())
