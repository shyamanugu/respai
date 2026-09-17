"""Proves a Data & Tools (07) tool satisfies Orchestration's `Tool` protocol
and works through `ToolRegistry` — the seam that component's README
originally left open for "Tools," now closed. Kept self-contained (its own
fakes) rather than importing component 07's test fixtures, matching how each
component's fakes are already independently defined.
"""
from unittest.mock import patch

from data_tools.retrieval.tool import RetrievalTool
from data_tools.types import SearchHit
from orchestration.tools import ToolRegistry

_CLIENTS_CONFIG = {
    "environments": {"dev": {"clients": {"acme": {"index_name": "idx-llmops-acme-dev"}}}}
}


class _FakeSearchBackend:
    def search(self, index_name, vector, top_k):
        return [SearchHit(content=f"hit from {index_name}", source="doc.pdf", score=1.0)]

    def upsert(self, index_name, documents):
        pass


class _FakeEmbeddingProvider:
    def embed(self, deployment, texts):
        return [[0.0] for _ in texts]


@patch("data_tools.client_index_registry._load_config", return_value=_CLIENTS_CONFIG)
def test_retrieval_tool_registers_and_invokes_through_tool_registry(_mock_config):
    tool = RetrievalTool(
        environment="dev",
        search_backend=_FakeSearchBackend(),
        provider_factory=lambda name: _FakeEmbeddingProvider(),
    )

    registry = ToolRegistry()
    registry.register(tool)

    assert "search_knowledge" in registry
    hits = registry.get("search_knowledge").invoke(client_id="acme", query="anything")
    assert hits[0].content == "hit from idx-llmops-acme-dev"
