# APIX platform configuration

APIX is onboarded to the platform **by config, not code** — no file under
`platform/services/**/src` is edited. These snippets mirror exactly what APIX
contributes to each platform config file (the platform files are the source of
truth; these are the discoverable, re-appliable record of APIX's deltas).

| Platform file (source of truth) | APIX adds |
|---|---|
| `platform/services/03-model-management/config/models.yaml` | reuses shared aliases — see `models.snippet.yaml` (no new aliases) |
| `platform/services/03-model-management/config/pricing.yaml` | reuses shipped rates; set AFNI's real Azure OpenAI rates before prod |
| `platform/services/06-guardrails/config/guardrails.yaml` | `usecases.apix` + `usecases.apix_chat` — see `guardrails.snippet.yaml` |
| `platform/services/04-evaluation-gate/config/gates.yaml` | `usecases.apix` + `usecases.apix_chat` — see `gates.snippet.yaml` |
| `platform/services/07-data-tools/config/clients.yaml` | `apix` index (reserved for future RAG) — see `clients.snippet.yaml` |

## Usecase ids
- **`apix`** — batch `ai_pipeline` LLM calls + the dashboard's `generate_coaching_insights`. PII flagged (never blocks a call), secrets blocked, prompt-injection off (internal transcripts).
- **`apix_chat`** — the user-facing chatbot. Prompt-injection **on**, PII flagged, secrets blocked, output length capped.

## Model aliases used (from models.yaml)
- `reason` → reasoning/analysis turns (deployment `null` ⇒ fail-open to `REASONING_MODEL_DEPLOYMENT`).
- `bulk` → high-volume/cheap turns, e.g. denoise + chatbot NL (deployment `null` ⇒ same fail-open).
- `judge` → LLM-as-judge for eval gate (`gpt-4o-mini`).

Set real deployment names in `models.yaml` (and real rates in `pricing.yaml`) to
activate registry-driven selection; until then the adapters fail open to the
`REASONING_MODEL_*` env values.
