# Serving & Hosting

## What this is
Packages Orchestration (08) as an HTTP service — the FastAPI wrapper, containerization, and Azure Container Apps deployment that component's README sketched as its "Future Deployment Path," now actually built rather than only planned. Generic and usecase-agnostic: it dispatches to whatever `Pipeline` a real usecase registers, and contains no pipeline logic of its own.

## The generic contract
```
GET  /healthz                    -> {"status": "ok"}
POST /pipelines/{name}/run       -> runs that Pipeline, returns its final State
```
`PipelineRegistry` maps a name to a constructed `Pipeline` instance. This component ships an empty registry (`src/serving/main.py`) — a real usecase copies that entrypoint, registers its actual pipelines, and that becomes the real deployed image. Deploying this component's own reference image as-is gives a working `/healthz` and a 404 on every `/pipelines/{name}/run` call, which is the honest, correct behavior for a registry with nothing in it.

## Why a generic contract, not a usecase-specific one
No usecase has defined what endpoints it actually needs yet (same gap Orchestration's ADR 0005 flagged for its own HTTP wrapper). Guessing at a usecase-specific API now risks building the wrong shape. `POST /pipelines/{name}/run` is deliberately the smallest possible contract that can front *any* registered pipeline — narrow enough to be honest about what's known today, wide enough that a real usecase's pipeline can be registered under it without changing this component's code.

## Testing without any Azure resource
`tests/test_app.py` uses FastAPI's `TestClient` against a real `orchestration.pipeline.Pipeline` and `orchestration.state.State`, with a trivial fake `Step` (not a real `ModelStep`) so this component's own test suite doesn't need Model Management credentials to prove the dispatch logic works. This is the same "prove the mechanism without needing the thing it will eventually run" pattern used throughout this platform.

## Canary rollout
`container-app.bicep` exposes `latestRevisionTrafficPercent` — a new revision starts at less than 100% of traffic, watched against health/eval signals (Evaluation Gate, component 04, once a real usecase and CI/CD's deployment job exist to drive that check), then ramped to 100% or rolled back. Not automated yet — this is the parameter a future CD pipeline would set, not a self-driving rollout mechanism today.

## What's blocked
- **No Managed Identity image-pull access** (`AcrPull` role) — blocked until RBAC role assignments are approved (Phase 0 queue), same posture as every other Azure resource in this platform. `container-app.bicep`'s placeholder image (`mcr.microsoft.com/k8se/quickstart`) is a public image specifically so the template is authorable and validatable now without needing a private registry credential.
- **No authentication on the HTTP endpoint** — Container Apps' built-in auth (Easy Auth) or an API Management front door both need an Entra ID app registration, the same access gap blocking CI/CD's (09) OIDC login. Deploying this as-is today would be an unauthenticated public endpoint; that's acceptable for validating the shape locally, not for anything real. See "Revisit When."
- **No real container image built or pushed** — CI/CD (09) doesn't build one; that requires the same Entra ID access its OIDC login needs, to push to a container registry.

## File layout
```
src/serving/
├── app.py                # create_app(registry) -> FastAPI — the generic wrapper
├── pipeline_registry.py    # PipelineRegistry — name -> Pipeline mapping
├── schemas.py               # RunPipelineRequest/RunPipelineResponse
└── main.py                   # reference entrypoint, empty registry — copy and adapt per usecase

tests/
└── test_app.py             # healthz, dispatch-and-return-state, unknown-pipeline-404

infra/
├── container-apps-environment.bicep   # Microsoft.App/managedEnvironments, wired to component 05's Log Analytics
├── container-app.bicep                 # the app itself, canary traffic-split param, public placeholder image
└── main.parameters.dev.json

Dockerfile                  # reference image — builds main.py's empty registry
```

## Prerequisites
- Component 08 (Orchestration) present as a sibling folder — this component imports `Pipeline`/`State` directly (see `docs/decisions/0004-python-package-naming.md`), which transitively requires 03, 02, and 05 on the path too
- Component 05 (Observability)'s Log Analytics workspace, if deploying `container-apps-environment.bicep` for real

## Local development
```bash
pip install -r requirements.txt
pytest
```
Every test runs against a fake `Step`, no Azure credentials, no network call. To run the reference server locally:
```bash
uvicorn serving.main:app --reload
```

Importable as the `serving` package, per `docs/decisions/0004-python-package-naming.md`.

## Setup (once ready to provision)
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/container-apps-environment.bicep \
  --parameters infra/main.parameters.dev.json \
    logAnalyticsCustomerId=<from az monitor log-analytics workspace show> \
    logAnalyticsSharedKey=<from az monitor log-analytics workspace get-shared-keys>

az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/container-app.bicep \
  --parameters infra/main.parameters.dev.json \
    containerAppsEnvironmentId=<output of the first command>
```
Nothing above has been run yet in this build. The default `containerImage` parameter is a public Microsoft quickstart image so the template deploys something real without needing a private registry credential — swap it for a usecase's actual image once one is built and pushed.

## Dependencies
- Depends on: component 08 (Pipeline/State), component 05 (Log Analytics workspace for the Container Apps environment)
- Depended on by: nothing yet — a real usecase is the expected first caller

## Cost notes
Azure Container Apps: consumption-based (vCPU-seconds + memory-seconds while running, scales to zero below `minReplicas`). `minReplicas: 1` here means a small always-on baseline cost once actually deployed; nothing is deployed yet.
