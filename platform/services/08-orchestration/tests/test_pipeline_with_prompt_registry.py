"""Proves ModelStep can source its prompt from Prompt Management (02) instead
of a raw template string — the seam `08-orchestration/README.md` originally
left open for "Prompt source," now closed.
"""
from pathlib import Path

from orchestration.pipeline import Pipeline
from orchestration.state import State
from orchestration.step import ModelStep
from prompt_management.registry import PromptRegistry

from .fakes import FakeModelProvider

_FIXTURE_PROMPTS_DIR = Path(__file__).parent / "fixtures" / "prompts"


def _fake_factory(responses):
    provider = FakeModelProvider(responses)
    return lambda provider_name: provider


def test_model_step_renders_prompt_via_registry():
    registry = PromptRegistry(prompt_dirs=[_FIXTURE_PROMPTS_DIR])
    factory = _fake_factory(
        {"sentiment was negative": "We're sorry to hear that — let's make it right."}
    )

    respond = ModelStep(
        name="respond",
        model_alias="reason",
        prompt_name="draft_reply",
        prompt_registry=registry,
        output_key="reply",
        input_keys=["sentiment"],
        provider_factory=factory,
    )

    pipeline = Pipeline(name="demo", steps=[respond])
    state = State()
    state.set("sentiment", "negative")

    result = pipeline.run(state, environment="dev")

    assert "sorry" in result.get("reply").lower()
