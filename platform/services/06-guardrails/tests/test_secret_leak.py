from guardrails.secret_leak import SecretLeakGuardrail


def test_blocks_aws_access_key_in_output():
    guardrail = SecretLeakGuardrail()
    result = guardrail.check_output("Here is a key: AKIAABCDEFGHIJKLMNOP")
    assert not result.allowed
    assert "aws_access_key" in result.reason


def test_blocks_private_key_header_in_output():
    guardrail = SecretLeakGuardrail()
    result = guardrail.check_output("-----BEGIN RSA PRIVATE KEY-----\nMIIEow...")
    assert not result.allowed


def test_never_blocks_input():
    guardrail = SecretLeakGuardrail()
    result = guardrail.check_input("AKIAABCDEFGHIJKLMNOP")
    assert result.allowed


def test_generic_api_key_disabled_by_default():
    guardrail = SecretLeakGuardrail()
    result = guardrail.check_output("token_" + "a" * 40)
    assert result.allowed


def test_generic_api_key_can_be_enabled():
    guardrail = SecretLeakGuardrail(enabled_categories={"generic_api_key": True})
    result = guardrail.check_output("a" * 40)
    assert not result.allowed
