"""Evaluator interface. Each `EvalCase` declares which evaluator it wants by
name (`case.evaluator`) — the gate looks it up by that name, it is never
auto-detected from the case's shape.
"""
from typing import Any, Protocol

from ..types import EvalCase, EvalResult


class Evaluator(Protocol):
    def evaluate(self, case: EvalCase, actual: Any) -> EvalResult:
        ...
