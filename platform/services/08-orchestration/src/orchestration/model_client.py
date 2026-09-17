"""Bridges Model Management's resolver output to an actual callable client.

Model Management (component 03) only resolves aliases to a provider name and
deployment — it deliberately does not call them (see its README). This is
where a resolved handle becomes something invokable. New providers are added
here as they're implemented in component 03's `providers/` package — this
file does not implement provider logic itself.
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
