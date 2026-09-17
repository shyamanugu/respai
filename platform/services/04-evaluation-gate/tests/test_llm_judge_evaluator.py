from evaluation_gate.evaluators.llm_judge import LLMJudgeEvaluator
from evaluation_gate.types import EvalCase

from .fakes import FakeJudgeProvider


def test_passes_on_pass_verdict():
    evaluator = LLMJudgeEvaluator(
        provider_factory=lambda name: FakeJudgeProvider("PASS: acknowledges frustration")
    )
    case = EvalCase(id="c1", input={}, evaluator="llm_judge", rubric="must acknowledge frustration")

    result = evaluator.evaluate(case, "I'm sorry to hear that, let's escalate this.")

    assert result.passed
    assert result.reason == "acknowledges frustration"


def test_fails_on_fail_verdict():
    evaluator = LLMJudgeEvaluator(
        provider_factory=lambda name: FakeJudgeProvider("FAIL: no escalation offered")
    )
    case = EvalCase(id="c1", input={}, evaluator="llm_judge", rubric="must offer escalation")

    result = evaluator.evaluate(case, "Sorry about that.")

    assert not result.passed
