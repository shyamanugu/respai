"""Search backend interface. `AzureAISearchBackend` is the real
implementation; `FakeSearchBackend` (tests/fakes.py) stands in for tests.
Neither one is aware of which client a query belongs to — that enforcement
happens one layer up, in `RetrievalTool`, via `client_index_registry`.
"""
from collections.abc import Sequence
from typing import Protocol

from ..types import SearchHit


class SearchBackend(Protocol):
    def search(self, index_name: str, vector: Sequence[float], top_k: int) -> list[SearchHit]:
        ...

    def upsert(self, index_name: str, documents: Sequence[dict]) -> None:
        ...
