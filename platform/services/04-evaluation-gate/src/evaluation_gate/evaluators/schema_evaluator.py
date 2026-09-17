"""Validates the actual output against the case's `output_schema` — the
first real consumer of Prompt Management's `output_schema` field, reserved
but unused since ADR 0006.
"""
from typing import Any

import jsonschema

from ..types import EvalCase, EvalResult


class SchemaEvaluator:
    def evaluate(self, case: EvalCase, actual: Any) -> EvalResult:
        try:
            jsonschema.validate(instance=actual, schema=case.output_schema)
            return EvalResult(case_id=case.id, passed=True)
        except jsonschema.ValidationError as exc:
            return EvalResult(case_id=case.id, passed=False, reason=exc.message)
