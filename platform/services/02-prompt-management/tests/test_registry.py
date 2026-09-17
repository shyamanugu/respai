"""Proves the registry mechanism works against a directory it doesn't own —
fixtures/usecase_demo stands in for a real usecase's own prompt folder, which
is the reusability proof: pointing the registry at a new directory is a
config change, not a code change.
"""
from pathlib import Path

import pytest
from prompt_management.registry import PromptRegistry
from prompt_management.types import MissingVariableError, PromptNotFoundError

_HERE = Path(__file__).parent
_SHARED_DIR = _HERE.parent / "prompts" / "shared"
_USECASE_DEMO_DIR = _HERE / "fixtures" / "usecase_demo" / "prompts"


def _registry() -> PromptRegistry:
    return PromptRegistry(
        prompt_dirs=[_USECASE_DEMO_DIR],
        fragment_dirs=[_SHARED_DIR],
    )


def test_resolve_loads_prompt_metadata():
    spec = _registry().resolve("classify_sentiment")
    assert spec.model_capability == "nano"
    assert spec.input_variables == ["message"]


def test_render_expands_fragments_and_variables():
    rendered = _registry().render("classify_sentiment", message="Where is my refund?")
    assert "Where is my refund?" in rendered
    assert "AFNI customer service agent" in rendered  # from safety_preamble
    assert "valid JSON object" in rendered  # from json_output_instruction
    assert "{{fragment:" not in rendered  # no unexpanded tokens left


def test_render_missing_variable_raises():
    with pytest.raises(MissingVariableError):
        _registry().render("classify_sentiment")


def test_resolve_unknown_prompt_raises():
    with pytest.raises(PromptNotFoundError):
        _registry().resolve("does_not_exist")


def test_list_prompts():
    assert _registry().list_prompts() == ["classify_sentiment"]
