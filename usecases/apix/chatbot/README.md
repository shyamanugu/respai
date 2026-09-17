# chatbot

APIX conversational analytics assistant. Answers natural-language questions via
**NL → SQL → NL** (LangGraph state machine) over Azure SQL + a SQLite cache.
FastAPI service; deploys as a Container **App** (+ a weekly ingestion **Job**).

## Run
```bash
uvicorn main:app --port 8000            # from this dir (PYTHONPATH=repo root)
python -m chatbot.ingestion             # weekly: Blob JSON → SQLite cache
```
Auth: every route requires a JWT minted by the dashboard (shared `CHAT_JWT_SECRET`);
`_enforce_user_scope` binds the token subject to the requested `user_id`. Generated
SQL is SELECT-only with an injected employee-scope filter.

## Structure

| Path | What it is |
|---|---|
| `main.py` | FastAPI app: `/chat_agent`, `/sessions/*`, `/feedback`, `/health`, ingestion router. |
| `orchestration/` | LangGraph graph + pipeline (classifier, memory, answer cache, source router). |
| `sources/` | `sqlite_source.py`, `azure_source.py` (NL→SQL against `vzw.rep_pivoted`). |
| `llm/` | `client.py` (the `generate_completion()` choke point), dictionaries, extraction. |
| `ingestion/` | Blob → SQLite ETL. |
| `core/` | config, schemas, sessions, JWT auth, tracing. |
| `prompts/` | `.txt` prompt templates. |
| **`llmops/`** | **Platform adapter.** |

## Platform integration (`llmops/`)

All model calls funnel through `llm/client.py::generate_completion()`. Onboarding
renamed the body to `_generate_completion_impl` (now returning text + token usage)
and wrapped it: the `llmops` adapter runs input/output guardrails (usecase
`apix_chat`, **prompt-injection enabled** — a hostile input is blocked with a safe
refusal), resolves the model alias (SQL turns → `reason`, narration → `bulk`), and
emits a traced `StepEvent` with real tokens + cost — all **fail-open**. `main.py`
initialises the tracer at startup and sets per-conversation attribution.
