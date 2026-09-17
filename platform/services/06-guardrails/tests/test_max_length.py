from guardrails.max_length import MaxLengthGuardrail


def test_blocks_input_over_limit():
    guardrail = MaxLengthGuardrail(max_input_chars=10)
    result = guardrail.check_input("this is definitely too long")
    assert not result.allowed


def test_allows_input_under_limit():
    guardrail = MaxLengthGuardrail(max_input_chars=100)
    result = guardrail.check_input("short")
    assert result.allowed


def test_no_limit_means_always_allowed():
    result = MaxLengthGuardrail().check_output("x" * 100000)
    assert result.allowed
