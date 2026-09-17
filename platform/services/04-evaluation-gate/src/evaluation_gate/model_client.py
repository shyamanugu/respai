"""Bridges Model Management's resolver output to an actual callable client,
for the one thing this component needs a model for: the LLM-as-judge
evaluator. Deliberately a small duplicate of the same bridge in Orchestration
(08) and Data & Tools (07) rather than a shared dependency on either — each
component that needs to call a resolved model owns this same small factory.
"""
from model_management.providers.azure_openai import AzureOpenAIProvider
from model_management.providers.base import ModelProvider

_PROVIDERS = {
    "azure_openai": AzureOpenAIProvider,
}


def get_provider(name: str) -> ModelProvider:
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"No provider client registered for '{name}'") from exc
    return provider_cls()
