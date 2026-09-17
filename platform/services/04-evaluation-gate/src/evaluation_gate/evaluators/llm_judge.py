"""Asks the `judge` model alias (see Model Management's models.yaml) to
score actual output against a case's rubric — for cases where "correct"
isn't exact-match-able: tone, completeness, whether escalation was offered.

The judge is asked for a single-line verdict ("PASS: <reason>" or
"FAIL: <reason>") rather than structured JSON, to keep parsing simple and
avoid depending on the judge model reliably producing strict JSON on every
call.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from model_management.model_router import resolve as resolve_model
from model_management.providers.base import ModelProvider
from model_management.types import ModelKind

from ..model_client import get_provider as _default_get_provider
from ..types import EvalCase, EvalResult

_JUDGE_PROMPT = """You are grading whether an AI system's output satisfies a rubric.

Rubric: {rubric}
Output: {actual}

Respond with exactly one line: "PASS: <short reason>" or "FAIL: <short reason>"."""


@dataclass
class LLMJudgeEvaluator:
    environment: str = "dev"
    judge_alias: str = "judge"
    provider_factory: Callable[[str], ModelProvider] = field(
        default=_default_get_provider, repr=False
    )

    def evaluate(self, case: EvalCase, actual: Any) -> EvalResult:
        handle = resolve_model(self.judge_alias, self.environment, expected_kind=ModelKind.CHAT)
        provider = self.provider_factory(handle.provider)

        prompt = _JUDGE_PROMPT.format(rubric=case.rubric, actual=actual)
        response = provider.chat(handle.deployment, [{"role": "user", "content": prompt}])
        verdict = response["content"].strip()

        passed = verdict.upper().startswith("PASS")
        reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
        return EvalResult(case_id=case.id, passed=passed, reason=reason)
