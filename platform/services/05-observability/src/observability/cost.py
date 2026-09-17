"""Computes cost in USD for a model call, using Model Management's
`config/pricing.yaml` as the single source of truth (see that file's header
comment) — figures are not duplicated here, only read.
"""
from pathlib import Path

import yaml

_PRICING_PATH = (
    Path(__file__).resolve().parents[3] / "03-model-management" / "config" / "pricing.yaml"
)


def _load_pricing() -> dict:
    with open(_PRICING_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def compute_cost(
    deployment: str, input_tokens: int, output_tokens: int, pricing: dict | None = None
) -> float:
    pricing = pricing if pricing is not None else _load_pricing()

    try:
        rates = pricing["deployments"][deployment]
    except (KeyError, TypeError):
        return 0.0

    input_cost = (input_tokens / 1000) * rates.get("input_per_1k", 0.0)
    output_cost = (output_tokens / 1000) * rates.get("output_per_1k", 0.0)
    return round(input_cost + output_cost, 6)
