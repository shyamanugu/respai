from observability.cost import compute_cost

_PRICING = {
    "deployments": {
        "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
    }
}


def test_computes_cost_from_pricing_table():
    cost = compute_cost("gpt-4o", input_tokens=1000, output_tokens=1000, pricing=_PRICING)
    assert cost == 0.02


def test_unknown_deployment_returns_zero():
    cost = compute_cost("unknown-model", input_tokens=1000, output_tokens=1000, pricing=_PRICING)
    assert cost == 0.0


def test_uses_real_pricing_file_by_default():
    """Proves the real cross-component file loads without error. Figures are
    placeholders (all $0.0) until AFNI's Azure OpenAI agreement is confirmed
    — see 03-model-management/config/pricing.yaml."""
    cost = compute_cost("gpt-4o-mini", input_tokens=1000, output_tokens=1000)
    assert cost == 0.0
