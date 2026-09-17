"""Proves the generic HTTP wrapper actually dispatches to a real
Orchestration `Pipeline`/`State` — a fake `Step` (not a real `ModelStep`)
keeps this component's own test suite independent of Model Management
credentials, while still exercising the real orchestration classes this
server is built to run.
"""
from dataclasses import dataclass

from fastapi.testclient import TestClient
from orchestration.pipeline import Pipeline
from orchestration.state import State
from serving.app import create_app
from serving.pipeline_registry import PipelineRegistry


@dataclass
class _EchoStep:
    name: str = "echo"

    def run(self, state: State, environment: str) -> State:
        state.set("echoed", state.get("input"))
        return state


def _client() -> TestClient:
    registry = PipelineRegistry()
    registry.register(Pipeline(name="demo", steps=[_EchoStep()]))
    return TestClient(create_app(registry))


def test_healthz():
    response = _client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_pipeline_dispatches_and_returns_state():
    response = _client().post("/pipelines/demo/run", json={"values": {"input": "hello"}})
    assert response.status_code == 200
    body = response.json()
    assert body["values"]["echoed"] == "hello"
    assert "session_id" in body


def test_unknown_pipeline_returns_404():
    response = _client().post("/pipelines/does_not_exist/run", json={})
    assert response.status_code == 404
