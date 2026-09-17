# Orchestration

## What this is
The engine that runs a pipeline of steps, threading shared state between them and calling models through Model Management (03) along the way. This is where "agents → model router → tools · guardrails" (from the platform architecture) actually executes.

**Current scope: a Python library, not a deployed service.** See `docs/decisions/0005-orchestration-library-first.md` for why, and the Future Deployment Path section below for how it eventually becomes one.

## What exists vs. what's a seam
All four originally-deferred integrations are wired in now — Prompt Management (02), Data & Tools (07), Guardrails (06), and Observability (05):

| Concern | Built now |
|---|---|
| Prompt source | `ModelStep.prompt_name` + `prompt_registry`, resolved via Prompt Management's `PromptRegistry` (falls back to a raw `prompt_template` string for quick one-off steps) — Component 02 |
| Tools | `RetrievalTool`, `SpeechToTextTool`, `TextToSpeechTool`, `HttpApiTool` registered into `ToolRegistry` — Component 07 |
| Guardrails | `ModelStep.guardrail` accepts any Guardrails (06) `CompositeGuardrail` — PII, blocklist, prompt-injection, secret-leak, max-length, and optional Content Safety checks — still defaults to `PassthroughGuardrail` if none is supplied — Component 06 |
| Tracing | `ModelStep.tracer` / `Pipeline.tracer` accept any Observability (05) `Tracer` — `InMemoryTracer` or `AzureMonitorTracer` — still default to `NullTracer` if none is supplied — Component 05 |

Tracing is the one case where Orchestration itself gained new code (the `tracer` parameter didn't exist before Observability was built) rather than an existing parameter another component dropped into — see `docs/decisions/0010-observability-scope.md`.

## File layout
```
src/orchestration/
├── state.py          # State: session_id + shared values dict
├── step.py             # Step protocol; ModelStep — builds prompt, resolves + calls a model, applies guardrail checks, records a StepEvent
├── pipeline.py          # Pipeline: ordered Steps, run(state, environment), records a PipelineEvent
├── model_client.py       # provider factory bridging component 03's resolver to an actual callable client
├── tools.py               # Tool protocol + ToolRegistry — populated with component 07's tools at usecase-assembly time
└── guardrails.py           # GuardrailCheck protocol + PassthroughGuardrail (until component 06)

tests/
├── __init__.py
├── fakes.py                                  # FakeModelProvider — canned responses, no live Azure call
├── fixtures/prompts/draft_reply.yaml           # demo prompt for the Prompt Management integration test
├── test_pipeline.py                            # 2-step demo, raw prompt_template, proves state threads end-to-end
├── test_pipeline_with_prompt_registry.py        # same demo shape, sourcing its prompt from Prompt Management instead
├── test_tools_registry.py                       # proves a Data & Tools RetrievalTool works through ToolRegistry
├── test_pipeline_with_guardrails.py              # proves a Guardrails CompositeGuardrail actually blocks/allows a step
└── test_pipeline_with_tracer.py                  # proves an Observability InMemoryTracer captures step + pipeline events, success and failure
```

## Prerequisites
- Component 03 (Model Management) present as a sibling folder — this component imports it directly (see `docs/decisions/0004-python-package-naming.md`)
- Component 02 (Prompt Management) present as a sibling folder, same reason — required only if a step uses `prompt_name` instead of `prompt_template`
- Component 07 (Data & Tools) present as a sibling folder, same reason — required only if a pipeline registers one of its tools
- Component 06 (Guardrails) present as a sibling folder, same reason — required only if a step's `guardrail` is set to something other than the default `PassthroughGuardrail`
- Component 05 (Observability) present as a sibling folder, same reason — required only if `tracer` is set to something other than the default `NullTracer`

## Local development
```bash
pip install -r requirements.txt
pytest
```
Both demo pipelines (classify sentiment → draft a response referencing it) run fully offline against `FakeModelProvider` — no `.env.local`, no deployed Azure OpenAI resource needed to prove the engine works.

## Future Deployment Path
Not built yet — documented now so the plan exists ahead of the work, per ADR 0005.

1. **Wrap the engine in a thin FastAPI app** once a real usecase or Serving & Hosting (10) defines the actual endpoint(s) needed (e.g., `POST /pipelines/{name}/run`). The wrapper stays thin — it should only translate HTTP in/out, never contain pipeline logic itself.
2. **Containerize** with a standard Python slim-image Dockerfile: install `requirements.txt`, copy `src/`, run via `uvicorn`.
3. **Deploy to Azure Container Apps**, in the resource group established by component 01, following the same naming convention (`ca-llmops-<environment>-<region>-<instance>`).
4. **Canary rollout** (component 10, Serving & Hosting) — new revision takes ~10% of traffic, watched against health/eval signals, ramps to 100% or auto-rolls-back.
5. **CI/CD** (component 09) builds the container image and runs the evaluation gate (component 04) before any deploy — a pipeline change is a reviewed, gated release, same principle as a model swap.
6. **Managed Identity** (from component 01) is attached to the Container App once its RBAC role assignments are approved (Phase 0 queue) — until then, any credential this service needs follows the same `.env.local` interim pattern as Model Management.

## Dependencies
- Depends on: component 01 (naming/tagging convention, future Managed Identity), component 03 (model resolution), component 02 (prompt resolution, when `prompt_name` is used), component 07 (tools registered into `ToolRegistry`), component 06 (guardrail checks, when set), component 05 (tracer, when set)
- Depended on by: every usecase, once one is onboarded; eventually Serving & Hosting (10) and CI/CD (09)

## Cost notes
No cost while this remains a library — nothing is deployed. Once containerized, cost follows Container Apps consumption pricing (vCPU/memory-seconds), not a fixed monthly charge.
