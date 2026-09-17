from orchestration.pipeline import Pipeline
from orchestration.state import State
from orchestration.step import ModelStep

from .fakes import FakeModelProvider


def _fake_factory(responses):
    provider = FakeModelProvider(responses)
    return lambda provider_name: provider


def test_two_step_pipeline_threads_state_between_steps():
    """Proves the engine runs end-to-end: step 1's output becomes step 2's
    input, with no live Azure call and nothing deployed."""
    responses = {
        "Classify the sentiment": "negative",
        "sentiment was negative": "We're sorry to hear that — let's make it right.",
    }
    factory = _fake_factory(responses)

    classify = ModelStep(
        name="classify",
        model_alias="nano",
        prompt_template="Classify the sentiment of: {input_text}",
        output_key="sentiment",
        input_keys=["input_text"],
        provider_factory=factory,
    )
    respond = ModelStep(
        name="respond",
        model_alias="reason",
        prompt_template="The sentiment was {sentiment}. Write a one-line reply.",
        output_key="reply",
        input_keys=["sentiment"],
        provider_factory=factory,
    )

    pipeline = Pipeline(name="demo", steps=[classify, respond])
    state = State()
    state.set("input_text", "This product broke after one day.")

    result = pipeline.run(state, environment="dev")

    assert result.get("sentiment") == "negative"
    assert "sorry" in result.get("reply").lower()
    assert result.session_id  # generated, ready for component 05 to attach traces to
