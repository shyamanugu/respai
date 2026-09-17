"""RetrievalTool — the reusable retrieval mechanism, and the enforcement
point for per-client data isolation. It never accepts a raw index name, only
a client_id, resolved through `client_index_registry`; there is no
constructor argument or keyword that lets a caller address another client's
index directly. See docs/decisions/0007-data-tools-scope.md for why a shared
Search service with one index per client — rather than a shared index with a
filter — is the isolation model here.
"""
from collections.abc import Callable
from dataclasses import dataclass, field

from model_management.model_router import resolve as resolve_model
from model_management.providers.base import ModelProvider
from model_management.types import ModelKind

from ..client_index_registry import resolve_client_index
from ..model_client import get_provider as _default_get_provider
from ..types import SearchHit
from .azure_search import AzureAISearchBackend
from .base import SearchBackend


@dataclass
class RetrievalTool:
    name: str = "search_knowledge"
    description: str = (
        "Retrieves the top-k relevant knowledge chunks for a given client_id, "
        "from that client's isolated Azure AI Search index only."
    )
    environment: str = "dev"
    embedding_alias: str = "embedding"
    default_top_k: int = 5
    search_backend: SearchBackend = field(default_factory=AzureAISearchBackend)
    provider_factory: Callable[[str], ModelProvider] = field(
        default=_default_get_provider, repr=False
    )

    def invoke(self, client_id: str, query: str, top_k: int | None = None) -> list[SearchHit]:
        index_name = resolve_client_index(client_id, self.environment)

        handle = resolve_model(self.embedding_alias, self.environment, expected_kind=ModelKind.EMBEDDING)
        provider = self.provider_factory(handle.provider)
        vector = provider.embed(handle.deployment, [query])[0]

        return self.search_backend.search(
            index_name=index_name, vector=vector, top_k=top_k or self.default_top_k
        )
