from pathlib import Path

from evaluation_gate.dataset_loader import load_dataset

_FIXTURE = Path(__file__).parent / "fixtures" / "usecase_demo" / "golden_dataset.jsonl"


def test_load_dataset_parses_all_cases():
    cases = load_dataset(_FIXTURE)
    assert len(cases) == 3
    assert cases[0].id == "sentiment_001"
    assert cases[0].evaluator == "exact_match"


def test_load_dataset_preserves_rubric_and_schema():
    cases = load_dataset(_FIXTURE)
    schema_case = next(c for c in cases if c.evaluator == "schema")
    assert schema_case.output_schema["type"] == "object"

    judge_case = next(c for c in cases if c.evaluator == "llm_judge")
    assert "escalation" in judge_case.rubric
