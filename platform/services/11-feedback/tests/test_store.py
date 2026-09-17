from feedback.store import InMemoryFeedbackStore, JsonlFileFeedbackStore
from feedback.types import FeedbackEvent


def test_in_memory_store_records_and_filters_by_session():
    store = InMemoryFeedbackStore()
    store.record(FeedbackEvent(session_id="s1", step_name="respond", rating="thumbs_up"))
    store.record(FeedbackEvent(session_id="s2", step_name="respond", rating="thumbs_down"))

    result = store.list_for_session("s1")

    assert len(result) == 1
    assert result[0].session_id == "s1"


def test_jsonl_store_persists_across_instances(tmp_path):
    path = tmp_path / "feedback.jsonl"
    JsonlFileFeedbackStore(path=path).record(
        FeedbackEvent(session_id="s1", step_name="respond", rating="edited", corrected_output="fixed")
    )

    reloaded = JsonlFileFeedbackStore(path=path).list_for_session("s1")

    assert len(reloaded) == 1
    assert reloaded[0].corrected_output == "fixed"


def test_jsonl_store_returns_empty_when_file_does_not_exist(tmp_path):
    store = JsonlFileFeedbackStore(path=tmp_path / "does_not_exist.jsonl")
    assert store.list_for_session("anything") == []
