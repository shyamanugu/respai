from observability.azure_monitor_tracer import AzureMonitorTracer
from observability.types import PipelineEvent, StepEvent


class _FakeLogger:
    def __init__(self) -> None:
        self.calls = []

    def info(self, msg, extra=None):
        self.calls.append((msg, extra))


def test_record_step_logs_with_custom_dimensions():
    fake_logger = _FakeLogger()
    tracer = AzureMonitorTracer(logger_factory=lambda: fake_logger)

    tracer.record_step(StepEvent(session_id="s1", step_name="classify", cost_usd=0.01))

    assert len(fake_logger.calls) == 1
    msg, extra = fake_logger.calls[0]
    assert msg == "step:classify"
    assert extra["custom_dimensions"]["session_id"] == "s1"
    assert extra["custom_dimensions"]["cost_usd"] == 0.01


def test_record_pipeline_logs_with_custom_dimensions():
    fake_logger = _FakeLogger()
    tracer = AzureMonitorTracer(logger_factory=lambda: fake_logger)

    tracer.record_pipeline(PipelineEvent(session_id="s1", pipeline_name="demo", step_count=2))

    msg, extra = fake_logger.calls[0]
    assert msg == "pipeline:demo"
    assert extra["custom_dimensions"]["step_count"] == 2


def test_logger_factory_only_called_once():
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return _FakeLogger()

    tracer = AzureMonitorTracer(logger_factory=factory)
    tracer.record_step(StepEvent(session_id="s1", step_name="a"))
    tracer.record_step(StepEvent(session_id="s1", step_name="b"))

    assert calls["count"] == 1
