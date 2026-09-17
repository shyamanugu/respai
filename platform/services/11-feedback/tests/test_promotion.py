import json

from feedback.promotion import promote_to_golden_dataset
from feedback.types import FeedbackEvent


def test_promotes_only_corrected_events(tmp_path):
    output_path = tmp_path / "golden_dataset.jsonl"
    events = [
        FeedbackEvent(
            session_id="s1",
            step_name="respond",
            rating="edited",
            original_input={"message": "where is my refund"},
            corrected_output="Your refund was processed on Monday.",
        ),
        FeedbackEvent(session_id="s2", step_name="respond", rating="thumbs_up"),
    ]

    written = promote_to_golden_dataset(events, output_path)

    assert written == 1
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    case = json.loads(lines[0])
    assert case["evaluator"] == "exact_match"
    assert case["expected"] == "Your refund was processed on Monday."
    assert case["input"] == {"message": "where is my refund"}


def test_appends_to_existing_file(tmp_path):
    output_path = tmp_path / "golden_dataset.jsonl"
    output_path.write_text('{"id": "existing", "input": {}, "expected": "x", "evaluator": "exact_match"}\n')

    event = FeedbackEvent(
        session_id="s1", step_name="respond", rating="edited", corrected_output="new expected"
    )
    promote_to_golden_dataset([event], output_path)

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_returns_zero_when_nothing_to_promote(tmp_path):
    output_path = tmp_path / "golden_dataset.jsonl"
    written = promote_to_golden_dataset(
        [FeedbackEvent(session_id="s1", step_name="respond", rating="thumbs_down")], output_path
    )
    assert written == 0
