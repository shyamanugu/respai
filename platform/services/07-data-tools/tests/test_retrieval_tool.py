"""The isolation proof: two clients' data lives in the fake backend under
different index names, and RetrievalTool — driven only by client_id — can
never return one client's hits for the other's request.
"""
from unittest.mock import patch

import pytest
from data_tools.client_index_registry import UnknownClientError
from data_tools.retrieval.tool import RetrievalTool
from data_tools.types import SearchHit

from .fakes import FakeEmbeddingProvider, FakeSearchBackend

_CLIENTS_CONFIG = {
    "environments": {
        "dev": {
            "clients": {
                "acme": {"index_name": "idx-llmops-acme-dev"},
                "globex": {"index_name": "idx-llmops-globex-dev"},
            }
        }
    }
}


def _tool() -> RetrievalTool:
    backend = FakeSearchBackend(
        seed={
            "idx-llmops-acme-dev": [SearchHit(content="Acme refund policy", source="acme.pdf", score=0.9)],
            "idx-llmops-globex-dev": [SearchHit(content="Globex refund policy", source="globex.pdf", score=0.9)],
        }
    )
    return RetrievalTool(
        environment="dev",
        search_backend=backend,
        provider_factory=lambda name: FakeEmbeddingProvider(),
    )


@patch("data_tools.client_index_registry._load_config", return_value=_CLIENTS_CONFIG)
def test_client_only_sees_its_own_index(_mock_config):
    tool = _tool()

    acme_hits = tool.invoke(client_id="acme", query="refund policy")
    globex_hits = tool.invoke(client_id="globex", query="refund policy")

    assert [h.content for h in acme_hits] == ["Acme refund policy"]
    assert [h.content for h in globex_hits] == ["Globex refund policy"]


@patch("data_tools.client_index_registry._load_config", return_value=_CLIENTS_CONFIG)
def test_unonboarded_client_cannot_be_queried(_mock_config):
    with pytest.raises(UnknownClientError):
        _tool().invoke(client_id="not_a_real_client", query="anything")
