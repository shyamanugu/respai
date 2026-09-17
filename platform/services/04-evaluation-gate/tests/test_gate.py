from pathlib import Path

import pytest
from evaluation_gate.dataset_loader import load_dataset
from evaluation_gate.evaluators.exact_match import ExactMatchEvaluator
from evaluation_gate.evaluators.llm_judge import LLMJudgeEvaluator
from evaluation_gate.evaluators.schema_evaluator import SchemaEvaluator
from evaluation_gate.gate import EvaluationGate
from evaluation_gate.types import EvalCase, UnknownEvaluatorError

from .fakes import FakeJudgeProvider

_FIXTURE = Path(__file__).parent / "fixtures" / "usecase_demo" / "golden_dataset.jsonl"


def _system_under_test(case):
    """Trivial stand-in proving the gate's aggregation logic, not a real
    pipeline's behavior — a real usecase would pass pipeline.run(...) here."""
    responses = {
        "sentiment_001": "negative",
        "format_001": {"sentiment": "negative"},
        "tone_001": "I'm sorry to hear that — let's get this escalated for you.",
    }
    return responses[case.id]


def _gate(judge_verdict: str) -> EvaluationGate:
    return EvaluationGate(
        environment="dev",
        evaluators={
            "exact_match": ExactMatchEvaluator(),
            "schema": SchemaEvaluator(),
            "llm_judge": LLMJudgeEvaluator(
                provider_factory=lambda name: FakeJudgeProvider(judge_verdict)
            ),
        },
    )


def test_gate_passes_when_every_case_passes():
    cases = load_dataset(_FIXTURE)
    result = _gate("PASS: acknowledges and escalates").run(
        usecase="demo_usecase", cases=cases, system_under_test=_system_under_test
    )

    assert result.passed
    assert result.pass_rate == 1.0


def test_gate_fails_below_threshold():
    cases = load_dataset(_FIXTURE)
    result = _gate("FAIL: missing escalation").run(
        usecase="demo_usecase", cases=cases, system_under_test=_system_under_test
    )

    assert not result.passed
    assert result.pass_rate < 1.0


def test_unknown_evaluator_raises():
    gate = EvaluationGate(evaluators={})
    case = EvalCase(id="x", input={}, evaluator="does_not_exist")

    with pytest.raises(UnknownEvaluatorError):
        gate.run(usecase="demo_usecase", cases=[case], system_under_test=lambda c: None)
