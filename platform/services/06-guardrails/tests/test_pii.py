from guardrails.pii import PIIGuardrail


def test_input_flags_but_allows_by_default():
    result = PIIGuardrail().check_input("Call me at 555-123-4567")
    assert result.allowed
    assert "phone" in result.reason


def test_output_blocks_by_default():
    result = PIIGuardrail().check_output("Their email is jane@example.com")
    assert not result.allowed
    assert "email" in result.reason


def test_category_mode_override_blocks_on_input():
    guardrail = PIIGuardrail(input_mode={"ssn": "block"})
    result = guardrail.check_input("SSN: 123-45-6789")
    assert not result.allowed


def test_category_mode_off_ignores_category():
    guardrail = PIIGuardrail(output_mode={"email": "off"})
    result = guardrail.check_output("contact jane@example.com")
    assert result.allowed


def test_clean_text_passes():
    result = PIIGuardrail().check_input("What's the status of my order?")
    assert result.allowed
    assert result.reason == ""
