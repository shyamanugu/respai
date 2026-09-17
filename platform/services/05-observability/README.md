# Observability

## What this is
Gives the `session_id` Orchestration (08) has been generating and threading through every pipeline run — "emitted nowhere yet," per that component's own README — somewhere to actually go. This component defines what a trace event looks like, how cost is computed from a model call, and where events can be sent: nowhere (`NullTracer`), in-process for tests and local debugging (`InMemoryTracer`), or Azure Monitor (`AzureMonitorTracer`).

## The seam this closes
Orchestration's `ModelStep` and `Pipeline` both gained a `tracer` parameter (default `NullTracer`, so nothing changes for existing callers) as part of building this component — the one place in this platform where the consuming component actually needed new code, rather than an existing parameter it could drop into. Every other integration so far (Prompt Management, Data & Tools, Guardrails) plugged into a parameter Orchestration already had; Tracing didn't have one yet, because there was nothing to trace to until now.

## What gets recorded
- **`StepEvent`** — one per `ModelStep.run()`: session ID, step name, model alias/provider/deployment, input/output token counts, cost (via `compute_cost`), latency, guardrail outcome, and error if the step failed.
- **`PipelineEvent`** — one per `Pipeline.run()`: session ID, pipeline name, step count, total latency, error if the run failed. Cost/latency roll-ups across steps are a query against whatever tracer backend is in use (trivial by hand against `InMemoryTracer.step_events`, a real Log Analytics query for `AzureMonitorTracer`), not recomputed in this class.

## Cost computation
`compute_cost(deployment, input_tokens, output_tokens)` reads Model Management's `config/pricing.yaml` — the single source of truth for pricing, not duplicated here (that file's own header comment says as much). An unknown deployment returns `0.0` rather than raising; this is telemetry, not a gate, and shouldn't fail a pipeline run over a missing pricing entry.

## Three tracers
| Tracer | Azure dependency | Use |
|---|---|---|
| `NullTracer` | none | default — nothing happens, mirrors `PassthroughGuardrail`'s role for guardrails |
| `InMemoryTracer` | none | tests, local debugging before Azure Monitor is provisioned |
| `AzureMonitorTracer` | Application Insights | ships events via `opencensus`'s `AzureLogHandler` attached to a standard Python logger |

`AzureMonitorTracer` lazily imports `opencensus` inside its default logger factory, so a usecase using only `NullTracer`/`InMemoryTracer` doesn't need that dependency installed. `logger_factory` is injectable (same pattern as `provider_factory`/`backend_factory` elsewhere), so its logic is fully testable with a fake logger — no live Application Insights resource needed to prove `record_step`/`record_pipeline` build the right payload.

## File layout
```
config/
└── observability.yaml               # per-environment tracer selection — informational, not auto-applied by Orchestration

src/observability/
├── types.py                          # StepEvent, PipelineEvent
├── cost.py                            # compute_cost() — reads component 03's pricing.yaml
├── tracer.py                          # Tracer protocol, NullTracer, InMemoryTracer
└── azure_monitor_tracer.py             # AzureMonitorTracer — real backend, lazy opencensus import

tests/
├── test_cost.py
├── test_tracer.py
└── test_azure_monitor_tracer.py        # proves payload logic via a fake logger, no opencensus needed
```

## Why `config/observability.yaml` isn't auto-applied
Unlike Model Management's `models.yaml` (read by a resolver every component calls) or Guardrails' `guardrails.yaml` (read by `build_guardrail()`), this file isn't consumed by any function in this component. `ModelStep`/`Pipeline` accept a `tracer` instance directly — whatever wires a usecase's pipeline together decides which tracer to construct, informed by this file's per-environment intent, the same way a usecase decides which prompt directory or guardrail policy to use. Building an auto-wiring function now would guess at a calling convention no usecase has established yet. See `docs/decisions/0010-observability-scope.md`.

## Prerequisites
None for `NullTracer`/`InMemoryTracer`. `.env.local` populated with `APPLICATIONINSIGHTS_CONNECTION_STRING` only if a usecase constructs `AzureMonitorTracer`.

## Local development
```bash
pip install -r requirements.txt
pytest
```
Every test runs against `InMemoryTracer` or a fake logger — no Azure credentials, no network call, no dependency on any other platform component (this is the second fully standalone component, alongside Guardrails).

Importable as the `observability` package, per `docs/decisions/0004-python-package-naming.md`.

## Setup (once ready to provision)
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/log-analytics.bicep \
  --parameters infra/main.parameters.dev.json

az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/application-insights.bicep \
  --parameters infra/main.parameters.dev.json logAnalyticsWorkspaceId=<output from the first command>
```
Two-step deploy — workspace-based Application Insights requires the Log Analytics workspace's resource ID as input, so it can't be a single template. Nothing above has been run yet in this build.

## Dependencies
- Depends on: component 03 (reads `pricing.yaml` for cost computation)
- Depended on by: Orchestration (08, `ModelStep.tracer` / `Pipeline.tracer`)

## Cost notes
`NullTracer`/`InMemoryTracer`: free. `AzureMonitorTracer`: Log Analytics ingestion + retention cost (data volume-based) once provisioned; nothing is provisioned yet.
