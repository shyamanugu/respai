from guardrails.azure_content_safety import AzureContentSafetyGuardrail

from .fakes import FakeContentSafetyBackend


def test_blocks_when_severity_meets_threshold():
    guardrail = AzureContentSafetyGuardrail(
        severity_threshold=4,
        backend_factory=lambda: FakeContentSafetyBackend({"Violence": 5}),
    )
    result = guardrail.check_output("some text")
    assert not result.allowed
    assert "Violence" in result.reason


def test_allows_below_threshold():
    guardrail = AzureContentSafetyGuardrail(
        severity_threshold=4,
        backend_factory=lambda: FakeContentSafetyBackend({"Violence": 2}),
    )
    result = guardrail.check_input("some text")
    assert result.allowed
