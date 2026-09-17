# ai_pipeline

APIX batch pipeline (CLI). Turns raw call transcripts into weekly per-employee
coaching intelligence. Deploys as an Azure Container Apps **Job**.

## Run
```bash
python -m ai_pipeline.main --mode telesales --date 2025-08-28
python -m ai_pipeline.main --program telesales --start 2025-08-01 --end 2025-08-07 --step analysis
```
Steps run in order: `denoise → analysis → summary → individual_metrics → kpi`
(`kpi` is pure aggregation, no LLM). See [`RUNBOOK.md`](RUNBOOK.md) for operations.

## Structure

| Path | What it is |
|---|---|
| `main.py` | Orchestrator: step registry, date-range loop, run summary. |
| `steps/` | The five steps. |
| `programs_config/` | Per-program (telesales/wcc/pso) Pydantic schemas, prompts, KPI defs + `base/`. |
| `services/` | `__init__.py` (the LLM `query()` choke point), `storage.py` (Blob/ADLS), `sql.py` (Azure SQL). |
| `utils/` | Throttling, coach mapping, transcript validation. |
| **`llmops/`** | **Platform adapter** (added for onboarding — see below). |

## Platform integration (`llmops/`)

Every LLM call funnels through `services/__init__.py::query()`. Onboarding renamed
the original to `_query_impl` and wrapped it: the `llmops` adapter resolves the
model alias (`denoise`→`bulk`, others→`reason`), runs input/output guardrails
(usecase `apix`), and emits a traced `StepEvent` (tokens + cost + latency) — all
**fail-open**. `main.py` sets run/step attribution and emits a `PipelineEvent`.
Traces land in the shared sink the ops-console reads. Domain logic is unchanged.
`llmops/feedback_gate.py` records feedback and promotes corrections to a golden set.
