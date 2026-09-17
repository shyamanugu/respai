from guardrails.blocklist import BlocklistGuardrail
from guardrails.composite import CompositeGuardrail
from guardrails.max_length import MaxLengthGuardrail
from guardrails.prompt_injection import PromptInjectionGuardrail


def test_passes_when_every_check_passes():
    guardrail = CompositeGuardrail(checks=[BlocklistGuardrail(terms=["banned"]), MaxLengthGuardrail()])
    result = guardrail.check_input("a perfectly normal message")
    assert result.allowed


def test_blocks_if_any_check_blocks():
    guardrail = CompositeGuardrail(
        checks=[BlocklistGuardrail(terms=["banned"]), PromptInjectionGuardrail()]
    )
    result = guardrail.check_input("this message contains a banned word")
    assert not result.allowed
    assert "banned" in result.reason.lower()


def test_runs_every_check_and_joins_reasons():
    guardrail = CompositeGuardrail(
        checks=[
            BlocklistGuardrail(terms=["banned"]),
            MaxLengthGuardrail(max_input_chars=5),
        ]
    )
    result = guardrail.check_input("this banned message is long")
    assert not result.allowed
    assert "Blocked term" in result.reason
    assert "exceeds" in result.reason
