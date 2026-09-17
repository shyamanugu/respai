# Data & Tools

## What this is
Concrete `Tool` implementations for the interface Orchestration (08) already defines: retrieval, the STT/TTS voice pipeline, and a generic way to call out to internal systems. Where Model Management resolves *models* and Prompt Management resolves *prompts*, this component gives Orchestration something to actually call for everything that isn't a model call — `search_knowledge`, `transcribe_audio`, `synthesize_speech`, and any HTTP-backed connector a usecase configures.

## Client data isolation
AFNI is a multi-client BPO — one client's retrieved data must never be reachable from another client's request. The model here: **one shared Azure AI Search service, one index per client**. `RetrievalTool` never accepts a raw index name, only a `client_id`, resolved through `client_index_registry.py` against `config/clients.yaml`. There is no parameter or code path that lets a caller address another client's index directly — the only way to change what a `client_id` resolves to is a reviewed edit to that config file. See `docs/decisions/0007-data-tools-scope.md` for why per-client index (not a shared index with a filter) was chosen, and how onboarding a new client works.

## Voice: two architectures, not one
Model Management (03) owns the Realtime API as a model deployment (`kind: realtime`). This component owns the separate STT/TTS pipeline — `SpeechToTextTool` and `TextToSpeechTool`, composed around whatever chat alias a usecase is already using. Both are available; which one a usecase picks is an application decision, not a platform one. See `docs/decisions/0003-model-management-scope.md` for the original boundary and `docs/decisions/0007-data-tools-scope.md` for this component's half of it.

## Generic connectors
`HttpApiTool` is a config-driven HTTP call — base URL, method, path template, and an auth header sourced from an environment variable, all from a YAML file. This is the reusable mechanism for "call some internal system" tools (ticketing, CRM lookups). This component does not implement any AFNI-specific system connector — that's usecase-owned integration logic. `tests/fixtures/connectors/example_ticketing.yaml` stands in for a usecase's own connector config, the same way Prompt Management's `tests/fixtures/usecase_demo/` stands in for a usecase's own prompts.

## File layout
```
config/
└── clients.yaml                     # client_id -> index_name, per environment — the isolation enforcement point

src/data_tools/
├── types.py                          # SearchHit
├── client_index_registry.py           # resolve_client_index(client_id, environment)
├── model_client.py                    # provider factory bridging component 03, for embedding queries
├── retrieval/
│   ├── base.py                        # SearchBackend protocol
│   ├── azure_search.py                 # AzureAISearchBackend — real implementation
│   └── tool.py                         # RetrievalTool ("search_knowledge")
├── speech/
│   ├── base.py                        # SpeechToTextBackend / TextToSpeechBackend protocols
│   ├── azure_speech.py                 # AzureSpeechBackend — real implementation, lazy SDK import
│   └── tools.py                        # SpeechToTextTool, TextToSpeechTool
└── connectors/
    └── http_api_tool.py                # HttpApiTool + load_connector_file()

scripts/
└── provision_client_index.py          # creates a new client's index schema — the operational half of onboarding

tests/
├── fakes.py                           # FakeSearchBackend, FakeEmbeddingProvider, FakeSpeechBackend
├── fixtures/connectors/                # demo connector config, stands in for usecase-owned config
├── test_client_index_registry.py
├── test_retrieval_tool.py              # the isolation proof — two clients, never cross-visible
├── test_speech_tools.py
└── test_http_api_tool.py
```

## Onboarding a new client (the reusability proof)
1. Add an entry to `config/clients.yaml` for the new `client_id` → index name (e.g. `idx-llmops-<client_id>-<environment>`) — reviewed via pull request, same as any other config change in this platform.
2. Run `scripts/provision_client_index.py --index-name idx-llmops-<client_id>-<environment>` to create the index schema against the shared Search service.
3. Ingest that client's documents (embed via Model Management's `embedding` alias, `upsert` into the new index — no ingestion pipeline is built yet, this is the manual path until one exists).

No change to `src/data_tools/` is required for any of this — the same argument Model Management and Prompt Management already make for their own resources.

## Prerequisites
- Component 03 (Model Management) present as a sibling folder — `RetrievalTool` resolves the `embedding` alias through it
- `.env.local` populated with `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_API_KEY` and `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` for anything that touches a real Azure backend (not needed for the test suite)

## Local development
```bash
pip install -r requirements.txt
pytest
```
Every test runs against `FakeSearchBackend` / `FakeEmbeddingProvider` / `FakeSpeechBackend` — no Azure credentials, no network call. `AzureSpeechBackend` lazily imports `azure-cognitiveservices-speech` inside `__init__`, so importing this package doesn't require that SDK unless something actually constructs it.

Importable as the `data_tools` package, per `docs/decisions/0004-python-package-naming.md`.

## Setup (once ready to provision)
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/azure-ai-search.bicep \
  --parameters infra/main.parameters.dev.json

az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/azure-speech.bicep \
  --parameters infra/main.parameters.dev.json
```
Nothing above has been run yet in this build — these are the commands to run when you're ready to actually provision. Bicep provisions the Search *service*; indexes are created per-client via `scripts/provision_client_index.py`, since index schemas are a data-plane concept, not an ARM resource.

## Dependencies
- Depends on: component 01 (naming/tagging, future Managed Identity), component 03 (embedding resolution)
- Depended on by: Orchestration (08, registers these tools into its `ToolRegistry`)

## Cost notes
Azure AI Search: Basic tier is a fixed monthly cost regardless of index count, which is part of why per-client index (rather than per-client service) was chosen — the marginal cost of a new client is $0 in infrastructure, only the index itself. Azure AI Speech: pay-per-use (per audio second processed), no fixed cost. 🌐 Quota/tier limits for Search (max indexes per service) are an external dependency to watch as the client count grows, not a permission gap.
