from observability.tracer import InMemoryTracer, NullTracer
from observability.types import PipelineEvent, StepEvent


def test_null_tracer_does_nothing():
    tracer = NullTracer()
    tracer.record_step(StepEvent(session_id="s1", step_name="classify"))
    tracer.record_pipeline(PipelineEvent(session_id="s1", pipeline_name="demo", step_count=1))
    # No assertion beyond "doesn't raise" — that's the entire contract.


def test_in_memory_tracer_collects_events():
    tracer = InMemoryTracer()
    tracer.record_step(StepEvent(session_id="s1", step_name="classify"))
    tracer.record_pipeline(PipelineEvent(session_id="s1", pipeline_name="demo", step_count=1))

    assert len(tracer.step_events) == 1
    assert tracer.step_events[0].step_name == "classify"
    assert len(tracer.pipeline_events) == 1
    assert tracer.pipeline_events[0].pipeline_name == "demo"
