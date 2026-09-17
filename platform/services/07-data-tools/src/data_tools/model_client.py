"""Bridges Model Management's resolver output to an actual callable client,
for the one thing this component needs a model for: embedding a query before
searching. Deliberately a small duplicate of Orchestration's
`model_client.py` rather than a shared dependency on Orchestration — tools
are consumed by Orchestration, so the dependency cannot run the other way.
New providers are added here as they're implemented in component 03's
`providers/` package.
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
