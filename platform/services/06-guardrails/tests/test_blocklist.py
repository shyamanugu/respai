from guardrails.blocklist import BlocklistGuardrail


def test_blocks_when_term_present():
    guardrail = BlocklistGuardrail(terms=["competitorbrand"])
    result = guardrail.check_output("Have you tried CompetitorBrand instead?")
    assert not result.allowed
    assert "competitorbrand" in result.reason.lower()


def test_allows_when_no_term_present():
    guardrail = BlocklistGuardrail(terms=["competitorbrand"])
    result = guardrail.check_output("Happy to help with your order.")
    assert result.allowed


def test_empty_blocklist_allows_everything():
    result = BlocklistGuardrail().check_input("anything at all")
    assert result.allowed
