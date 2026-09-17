# Model Management

## What this is
Config-as-code resolution from a stable task alias (e.g. `reason`, `bulk`, `embedding`) to an actual model deployment, provider, and capability kind. Nothing in the platform ever hard-codes a model name — it asks this component for an alias, and a config change here (reviewed via pull request, gated by the evaluation gate) is how a model gets swapped.

This component resolves models. It does not call them — invoking a resolved model happens in Orchestration (component 08).

## Scope boundary: voice
Two legitimate voice architectures exist: a single Realtime API model, or a Speech-to-Text → chat model → Text-to-Speech pipeline. This component only owns the first — it is a model deployment like any other (`kind: realtime`). The pipeline pattern is not a "model" in this component's sense; it belongs to Data & Tools (component 07) as tools composed around an existing chat alias. See `docs/decisions/0003-model-management-scope.md`.

## Azure resources used
- **Azure OpenAI account** (Cognitive Services, kind `OpenAI`)
- **Model deployments** within it — one per configured alias that uses this provider

🌐 Quota for each deployed model, in the target region, is an external dependency (support ticket), not a permission gap.

## Prerequisites
- Component 01 (Repo & Foundation) deployed — this reuses its resource group and naming convention
- `.env.local` populated with a real Azure OpenAI endpoint + API key for local testing (never committed — see `docs/decisions/0001-repo-foundation-approach.md`)

## Setup
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/azure-openai.bicep \
  --parameters infra/main.parameters.dev.json
```
Nothing above has been run yet in this build — these are the commands to run when you're ready to actually provision.

## Configuration
- `config/models.yaml` — alias → `{provider, deployment, kind}`, one block per environment
- `config/pricing.yaml` — deployment → cost per 1,000 input/output tokens (consumed by Observability, component 05 — not duplicated there)
- `.env.local` — `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` (real values, gitignored, created locally by you — not committed by this build)

Deployment names in `config/models.yaml` are placeholders (`gpt-4o`, `gpt-4o-mini`, `text-embedding-3-large`) until AFNI's actually-approved model list is confirmed. Changing them is a config edit, nothing else.

## Local development
```bash
pip install -r requirements.txt
pytest
```
The resolver (`src/model_management/model_router.py`) only reads config — tests run with no Azure credentials and no network call.

Importable as the `model_management` package (not `src`) — every component's package is named after its function, not the generic folder it lives in, so components can import each other without colliding. See `docs/decisions/0004-python-package-naming.md`.

## Deployment
Manual for now, via the Azure CLI command above. Once CI/CD (component 09) exists, a `models.yaml` change becomes a pull request that must pass the evaluation gate (component 04) before it deploys — a model swap is a reviewed, gated change, never a live edit.

## Dependencies
- Depends on: component 01 (resource group, naming, Managed Identity)
- Depended on by: Orchestration (08, calls resolved models), Observability (05, reads the pricing table), Evaluation Gate (04, uses the `judge` alias)

## Cost notes
No fixed cost for the account itself. Token consumption is billed per the pricing table; Provisioned Throughput (PTU) is a future option once volume is predictable enough to justify reserved capacity over pay-as-you-go.
