"""Data shapes for a golden-dataset case, a per-case evaluation result, and
the aggregated gate decision.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalCase:
    id: str
    input: dict
    evaluator: str
    expected: Any = None
    rubric: str | None = None
    output_schema: dict | None = None


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    reason: str = ""


@dataclass
class GateResult:
    passed: bool
    pass_rate: float
    threshold: float
    results: list[EvalResult] = field(default_factory=list)


class UnknownEvaluatorError(KeyError):
    pass
