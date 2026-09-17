from evaluation_gate.evaluators.exact_match import ExactMatchEvaluator
from evaluation_gate.types import EvalCase


def test_passes_on_exact_match():
    case = EvalCase(id="c1", input={}, evaluator="exact_match", expected="negative")
    result = ExactMatchEvaluator().evaluate(case, "negative")
    assert result.passed


def test_fails_on_mismatch():
    case = EvalCase(id="c1", input={}, evaluator="exact_match", expected="negative")
    result = ExactMatchEvaluator().evaluate(case, "positive")
    assert not result.passed
    assert "expected" in result.reason
