"""Builds a `CompositeGuardrail` from config/guardrails.yaml — a usecase not
listed there gets the `defaults` block. This is the reusability mechanism:
onboarding a new usecase's guardrail policy is a config entry, not a code
change, same shape as every other component in this platform.
"""
from pathlib import Path

import yaml

from .blocklist import BlocklistGuardrail
from .composite import CompositeGuardrail
from .max_length import MaxLengthGuardrail
from .pii import PIIGuardrail
from .prompt_injection import PromptInjectionGuardrail
from .secret_leak import SecretLeakGuardrail

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "guardrails.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_policy(usecase: str, environment: str) -> dict:
    config = _load_config()
    try:
        return config["usecases"][usecase][environment]
    except (KeyError, TypeError):
        return config.get("defaults", {})


def build_guardrail(
    usecase: str, environment: str, include_azure_content_safety: bool | None = None
) -> CompositeGuardrail:
    policy = _resolve_policy(usecase, environment)
    checks = []

    pii_policy = policy.get("pii", {})
    checks.append(
        PIIGuardrail(
            input_mode=pii_policy.get("input_mode", {}),
            output_mode=pii_policy.get("output_mode", {}),
        )
    )

    blocklist_policy = policy.get("blocklist", {})
    checks.append(BlocklistGuardrail(terms=blocklist_policy.get("terms", [])))

    if policy.get("prompt_injection", {}).get("enabled", True):
        checks.append(PromptInjectionGuardrail())

    if policy.get("secret_leak", {}).get("enabled", True):
        checks.append(SecretLeakGuardrail())

    max_length_policy = policy.get("max_length", {})
    checks.append(
        MaxLengthGuardrail(
            max_input_chars=max_length_policy.get("max_input_chars"),
            max_output_chars=max_length_policy.get("max_output_chars"),
        )
    )

    cs_policy = policy.get("azure_content_safety", {})
    use_azure_cs = (
        include_azure_content_safety
        if include_azure_content_safety is not None
        else cs_policy.get("enabled", False)
    )
    if use_azure_cs:
        from .azure_content_safety import AzureContentSafetyGuardrail

        checks.append(AzureContentSafetyGuardrail(severity_threshold=cs_policy.get("severity_threshold", 4)))

    return CompositeGuardrail(checks=checks)
