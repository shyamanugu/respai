"""Passes only when the actual output equals the case's expected value
exactly. The cheapest evaluator — use for deterministic cases (classification
labels, structured fields), not for anything requiring judgment.
"""
from typing import Any

from ..types import EvalCase, EvalResult


class ExactMatchEvaluator:
    def evaluate(self, case: EvalCase, actual: Any) -> EvalResult:
        passed = actual == case.expected
        reason = "" if passed else f"expected {case.expected!r}, got {actual!r}"
        return EvalResult(case_id=case.id, passed=passed, reason=reason)
