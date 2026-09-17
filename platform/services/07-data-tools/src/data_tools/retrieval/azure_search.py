"""Azure AI Search backend. Reads credentials from AZURE_SEARCH_ENDPOINT and
AZURE_SEARCH_API_KEY (see .env.local — never commit real values to .env).
One shared Search service hosts every client's index; which index a given
call reaches is decided entirely by the caller-supplied `index_name` — this
class has no concept of a client, by design, so isolation can't be bypassed
by adding a shortcut here.

Managed-identity auth (keyless) is deferred until the RBAC role assignment
in docs/checklist/BUILD-CHECKLIST.md is granted; this adapter's public
interface will not need to change when that happens, only how it
authenticates internally.
"""
import os
from collections.abc import Sequence

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

from ..types import SearchHit


class AzureAISearchBackend:
    def __init__(self) -> None:
        self._endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        self._credential = AzureKeyCredential(os.environ["AZURE_SEARCH_API_KEY"])
        self._clients: dict[str, SearchClient] = {}

    def _client_for(self, index_name: str) -> SearchClient:
        if index_name not in self._clients:
            self._clients[index_name] = SearchClient(self._endpoint, index_name, self._credential)
        return self._clients[index_name]

    def search(self, index_name: str, vector: Sequence[float], top_k: int) -> list[SearchHit]:
        client = self._client_for(index_name)
        query = VectorizedQuery(vector=list(vector), k_nearest_neighbors=top_k, fields="embedding")
        results = client.search(search_text=None, vector_queries=[query], top=top_k)
        return [
            SearchHit(
                content=result.get("content", ""),
                source=result.get("source", ""),
                score=result.get("@search.score", 0.0),
            )
            for result in results
        ]

    def upsert(self, index_name: str, documents: Sequence[dict]) -> None:
        client = self._client_for(index_name)
        client.upload_documents(documents=list(documents))
