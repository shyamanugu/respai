"""An ordered sequence of Steps, run against one shared State. Emits one
`PipelineEvent` per run via `tracer` (defaults to `NullTracer`, so nothing
changes for a caller that doesn't wire in real observability) — this is the
"Tracing" seam this component's README listed as open, closed by
Observability (05).

Cost/latency roll-ups across steps are not recomputed here — each step's own
tracer records its own `StepEvent` independently (pass the same tracer
instance to both `Pipeline` and each `ModelStep` to capture everything in
one place). Summing per-step costs into a total is a query against whatever
tracer backend is in use — trivial by hand against
`InMemoryTracer.step_events`, a real query against Azure Monitor for
`AzureMonitorTracer` — not something duplicated in this class.
"""
import time
from dataclasses import dataclass, field

from observability.tracer import NullTracer, Tracer
from observability.types import PipelineEvent

from .state import State
from .step import Step


@dataclass
class Pipeline:
    name: str
    steps: list[Step]
    tracer: Tracer = field(default_factory=NullTracer)

    def run(self, state: State, environment: str) -> State:
        start = time.perf_counter()
        error = None

        try:
            for step in self.steps:
                state = step.run(state, environment)
            return state
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            self.tracer.record_pipeline(
                PipelineEvent(
                    session_id=state.session_id,
                    pipeline_name=self.name,
                    step_count=len(self.steps),
                    total_latency_ms=(time.perf_counter() - start) * 1000,
                    error=error,
                )
            )
