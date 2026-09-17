"""EvaluationGate — runs a golden dataset's cases through a caller-supplied
system-under-test, scores each with the evaluator its case declares, and
aggregates a pass/fail decision against a configured threshold.

`system_under_test` is a plain callable (`EvalCase -> Any`), not a
dependency on Orchestration's `Pipeline` type — this keeps the harness
usable for testing a single prompt, a single model swap, or a full pipeline
without this component needing to know which. See
docs/decisions/0008-evaluation-gate-scope.md.
"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .evaluators.base import Evaluator
from .evaluators.exact_match import ExactMatchEvaluator
from .evaluators.llm_judge import LLMJudgeEvaluator
from .evaluators.schema_evaluator import SchemaEvaluator
from .types import EvalCase, GateResult, UnknownEvaluatorError

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "gates.yaml"
_DEFAULT_THRESHOLD = 1.0


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_threshold(usecase: str, environment: str) -> float:
    config = _load_config()
    try:
        return config["usecases"][usecase][environment]["pass_threshold"]
    except (KeyError, TypeError):
        return _DEFAULT_THRESHOLD


@dataclass
class EvaluationGate:
    environment: str = "dev"
    evaluators: dict[str, Evaluator] | None = None

    def __post_init__(self) -> None:
        if self.evaluators is None:
            self.evaluators = {
                "exact_match": ExactMatchEvaluator(),
                "schema": SchemaEvaluator(),
                "llm_judge": LLMJudgeEvaluator(environment=self.environment),
            }

    def run(
        self,
        usecase: str,
        cases: list[EvalCase],
        system_under_test: Callable[[EvalCase], Any],
        threshold: float | None = None,
    ) -> GateResult:
        results = []
        for case in cases:
            try:
                evaluator = self.evaluators[case.evaluator]
            except KeyError as exc:
                raise UnknownEvaluatorError(
                    f"Case '{case.id}' declares unknown evaluator '{case.evaluator}'"
                ) from exc

            actual = system_under_test(case)
            results.append(evaluator.evaluate(case, actual))

        pass_rate = sum(1 for r in results if r.passed) / len(results) if results else 1.0
        effective_threshold = (
            threshold if threshold is not None else _resolve_threshold(usecase, self.environment)
        )

        return GateResult(
            passed=pass_rate >= effective_threshold,
            pass_rate=pass_rate,
            threshold=effective_threshold,
            results=results,
        )
