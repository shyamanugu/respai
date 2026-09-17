"""Tracer interface Orchestration's `ModelStep`/`Pipeline` emit events
through. `NullTracer` is the default — no-op, the same role
`PassthroughGuardrail` plays for guardrails — so nothing changes for a
caller that doesn't wire in real observability. `InMemoryTracer` collects
events for tests and for local debugging before Azure Monitor is
provisioned.
"""
from dataclasses import dataclass, field
from typing import Protocol

from .types import PipelineEvent, StepEvent


class Tracer(Protocol):
    def record_step(self, event: StepEvent) -> None:
        ...

    def record_pipeline(self, event: PipelineEvent) -> None:
        ...


class NullTracer:
    def record_step(self, event: StepEvent) -> None:
        pass

    def record_pipeline(self, event: PipelineEvent) -> None:
        pass


@dataclass
class InMemoryTracer:
    step_events: list[StepEvent] = field(default_factory=list)
    pipeline_events: list[PipelineEvent] = field(default_factory=list)

    def record_step(self, event: StepEvent) -> None:
        self.step_events.append(event)

    def record_pipeline(self, event: PipelineEvent) -> None:
        self.pipeline_events.append(event)
