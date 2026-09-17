# Guardrails

## What this is
Concrete implementations of the `check_input`/`check_output` shape Orchestration's `ModelStep` has been accepting since it was built, previously only satisfied by the no-op `PassthroughGuardrail`. This component gives it something real to call: five free heuristic checks that need no Azure resource at all, plus one optional Azure AI Content Safety check for anyone who provisions it.

## The checks
| Check | Applies to | Azure dependency | Default |
|---|---|---|---|
| `PIIGuardrail` | input + output | none (regex) | flags PII on input, blocks it on output — per-category override |
| `BlocklistGuardrail` | input + output | none | no terms — usecase supplies its own |
| `PromptInjectionGuardrail` | input only | none (heuristic patterns) | on |
| `SecretLeakGuardrail` | output only | none (regex) | on, except noisy `generic_api_key` category |
| `MaxLengthGuardrail` | input + output | none | no cap unless configured |
| `AzureContentSafetyGuardrail` | input + output | Azure AI Content Safety | off — opt in per usecase |

`CompositeGuardrail` combines any of these into one object satisfying Orchestration's guardrail shape exactly — a drop-in replacement for `PassthroughGuardrail`, no change needed in Orchestration itself. Every check runs on every call, even after one blocks, so every reason is available (useful once Observability, component 05, exists to log them), not just whichever check happened to trip first.

## Why PII defaults differ by direction
PII arriving in *input* is often legitimate — a customer providing their own phone number, account number, or email as part of a normal request. Blocking on that would break the product. PII appearing in *output* — the model repeating back another customer's details, or inventing a plausible-looking SSN — is a much stronger signal something's wrong. So `PIIGuardrail` defaults to `flag` (allowed through, but recorded) on input and `block` on output, per category, overridable in `config/guardrails.yaml`.

## Reusability: policy in config, mechanism in code
Same shape as every component so far: onboarding a new usecase's guardrail policy — which blocklist terms, which PII categories to block vs. flag, whether Content Safety is enabled, what the length caps are — is a `config/guardrails.yaml` entry, not a code change. `build_guardrail(usecase, environment)` resolves that policy into a ready-to-use `CompositeGuardrail`. A usecase not listed gets the `defaults` block.

## What isn't built, and why
- **Redaction** (rewriting text instead of blocking it) — Orchestration's `GuardrailCheck` protocol only returns `allowed`/`reason`, it can't hand back modified text. Adding that would be a breaking change to an interface another component (08) already depends on; not made unilaterally from here. See "Revisit When."
- **Content Safety's Prompt Shields** (a stronger, Azure-backed jailbreak detector, as an alternative to the heuristic `PromptInjectionGuardrail`) — its exact SDK call shape wasn't verified against a live resource at the time this was written, and this platform doesn't author confidently-guessed integrations. `AzureContentSafetyGuardrail` only wraps the well-documented `analyze_text` harm-category operation.
- **Topic/scope restriction** ("is this question even about what the usecase is for") — would need an LLM-judge-style check (cost, latency), and no usecase has defined what "off-topic" means yet. Building it speculatively risks guessing wrong.
- **Rate limiting / abuse detection** — belongs to Serving & Hosting (10) or API Management, not this component; same boundary reasoning as the voice split between components 03 and 07.

See `docs/decisions/0009-guardrails-scope.md` for the full reasoning.

## File layout
```
config/
└── guardrails.yaml                   # per usecase/environment policy — the reusability mechanism

src/guardrails/
├── types.py                          # CheckResult, GuardrailCheck (mirrors Orchestration's shape, not imported from it)
├── pii.py                            # PIIGuardrail
├── blocklist.py                       # BlocklistGuardrail
├── prompt_injection.py                 # PromptInjectionGuardrail
├── secret_leak.py                      # SecretLeakGuardrail
├── max_length.py                       # MaxLengthGuardrail
├── azure_content_safety_backend.py      # AzureContentSafetyBackend — real Azure call, lazy SDK import
├── azure_content_safety.py               # AzureContentSafetyGuardrail — wraps the backend as a GuardrailCheck
├── composite.py                          # CompositeGuardrail — combines any of the above
└── builder.py                             # build_guardrail(usecase, environment) — config -> CompositeGuardrail

tests/
├── fakes.py                           # FakeContentSafetyBackend
├── test_pii.py
├── test_blocklist.py
├── test_prompt_injection.py
├── test_secret_leak.py
├── test_max_length.py
├── test_azure_content_safety.py
├── test_composite.py
└── test_builder.py                    # proves config-driven policy resolution, including the "unlisted usecase" default path
```

## Prerequisites
None for the free checks. `.env.local` populated with `AZURE_CONTENT_SAFETY_ENDPOINT` / `AZURE_CONTENT_SAFETY_API_KEY` only if a usecase enables `azure_content_safety`.

## Local development
```bash
pip install -r requirements.txt
pytest
```
Every test runs against the real heuristic checks directly (they're pure Python, no backend to fake) or against `FakeContentSafetyBackend` for the one Azure-backed check — no Azure credentials, no network call, no dependency on Model Management or any other component.

Importable as the `guardrails` package, per `docs/decisions/0004-python-package-naming.md`.

## Setup (once ready to provision Content Safety)
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/azure-content-safety.bicep \
  --parameters infra/main.parameters.dev.json
```
Nothing above has been run yet in this build. The five heuristic checks need no Azure resource at all and work today with zero setup.

## Dependencies
- Depends on: nothing (no other platform component is a prerequisite)
- Depended on by: Orchestration (08, `ModelStep.guardrail` accepts a `CompositeGuardrail` from here)

## Cost notes
The five heuristic checks are free — pure Python, no external call. `AzureContentSafetyGuardrail` costs per text record analyzed, only when a usecase opts in.
