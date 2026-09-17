"""Proves Observability's (05) InMemoryTracer actually captures both
per-step and pipeline-level events from a real run — closing the "Tracing"
seam this component's README listed as open. Also proves a blocked step
still records a StepEvent with the error/guardrail reason attached, since
telemetry on a failed run matters at least as much as on a successful one.
"""
import pytest
from guardrails.blocklist import BlocklistGuardrail
from guardrails.composite import CompositeGuardrail
from observability.tracer import InMemoryTracer
from orchestration.guardrails import GuardrailBlockedError
from orchestration.pipeline import Pipeline
from orchestration.state import State
from orchestration.step import ModelStep

from .fakes import FakeModelProvider


def _fake_factory(responses):
    provider = FakeModelProvider(responses)
    return lambda provider_name: provider


def test_tracer_captures_step_and_pipeline_events_on_success():
    tracer = InMemoryTracer()
    step = ModelStep(
        name="respond",
        model_alias="reason",
        prompt_template="{message}",
        output_key="reply",
        input_keys=["message"],
        tracer=tracer,
        provider_factory=_fake_factory({"hello": "hi there"}),
    )
    state = State()
    state.set("message", "hello")

    Pipeline(name="demo", steps=[step], tracer=tracer).run(state, environment="dev")

    assert len(tracer.step_events) == 1
    event = tracer.step_events[0]
    assert event.step_name == "respond"
    assert event.model_alias == "reason"
    assert event.error is None
    assert event.guardrail_allowed

    assert len(tracer.pipeline_events) == 1
    assert tracer.pipeline_events[0].pipeline_name == "demo"
    assert tracer.pipeline_events[0].error is None


def test_tracer_captures_error_on_guardrail_block():
    tracer = InMemoryTracer()
    step = ModelStep(
        name="respond",
        model_alias="reason",
        prompt_template="{message}",
        output_key="reply",
        input_keys=["message"],
        guardrail=CompositeGuardrail(checks=[BlocklistGuardrail(terms=["bannedword"])]),
        tracer=tracer,
        provider_factory=_fake_factory({}),
    )
    state = State()
    state.set("message", "this has a bannedword in it")

    with pytest.raises(GuardrailBlockedError):
        Pipeline(name="demo", steps=[step], tracer=tracer).run(state, environment="dev")

    assert len(tracer.step_events) == 1
    event = tracer.step_events[0]
    assert not event.guardrail_allowed
    assert event.error is not None

    assert len(tracer.pipeline_events) == 1
    assert tracer.pipeline_events[0].error is not None
