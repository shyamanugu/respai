from evaluation_gate.evaluators.schema_evaluator import SchemaEvaluator
from evaluation_gate.types import EvalCase

_SCHEMA = {
    "type": "object",
    "properties": {"sentiment": {"type": "string"}},
    "required": ["sentiment"],
}


def test_passes_when_actual_matches_schema():
    case = EvalCase(id="c1", input={}, evaluator="schema", output_schema=_SCHEMA)
    result = SchemaEvaluator().evaluate(case, {"sentiment": "negative"})
    assert result.passed


def test_fails_when_required_field_missing():
    case = EvalCase(id="c1", input={}, evaluator="schema", output_schema=_SCHEMA)
    result = SchemaEvaluator().evaluate(case, {})
    assert not result.passed
