"""Creates a new client's Azure AI Search index against the shared service.

This is the operational step that pairs with adding a client to
config/clients.yaml — that config entry says a client_id maps to an index
name, this script is what actually creates that index. It is not run
automatically by anything; run it by hand (or from a future CI/CD pipeline,
component 09) once a client is ready to onboard.

Usage:
    python scripts/provision_client_index.py --index-name idx-llmops-acme-dev
"""
import argparse
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

# Matches text-embedding-3-large's output dimensionality (see component 03's
# models.yaml). Update this if the embedding alias's model changes.
_EMBEDDING_DIMENSIONS = 3072


def build_index_schema(index_name: str) -> SearchIndex:
    return SearchIndex(
        name=index_name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=_EMBEDDING_DIMENSIONS,
                vector_search_profile_name="default-vector-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
            profiles=[
                VectorSearchProfile(
                    name="default-vector-profile", algorithm_configuration_name="default-hnsw"
                )
            ],
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-name", required=True, help="e.g. idx-llmops-acme-dev")
    args = parser.parse_args()

    client = SearchIndexClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_API_KEY"]),
    )
    client.create_index(build_index_schema(args.index_name))
    print(f"Created index '{args.index_name}'")


if __name__ == "__main__":
    main()
