# PII Handling Policy

## Enforcement mechanism: Guardrails (06)
`PIIGuardrail` (see `docs/decisions/0009-guardrails-scope.md`) is where PII is actually detected and acted on: **flagged** (allowed through, recorded) on input by default, **blocked** on output by default, per category (email, phone, SSN, credit card), overridable per usecase in `config/guardrails.yaml`. This document is the policy layer on top of that mechanism — what the defaults mean and where PII risk exists elsewhere in the platform that Guardrails alone doesn't cover.

## Why input defaults to flag, not block
A customer providing their own phone number or account details as part of a normal request is legitimate, expected input — blocking it would break the product. The risk this platform actually guards against is the model **repeating or inventing** PII in its output (another customer's details, a fabricated SSN), which is why output defaults to block.

## A finding from this build, now made deliberate: Observability doesn't log raw content
Observability's (05) `StepEvent` — the trace record for every model call — captures `model_alias`, `provider`, `deployment`, token counts, cost, latency, and guardrail outcome. **It does not capture the actual prompt text or model response.** This wasn't originally a deliberate privacy decision when `StepEvent` was designed (see ADR 0010) — it happened to avoid a PII-in-telemetry risk as a side effect of keeping the event shape focused on cost/performance metrics, not content. Documenting it here makes it a deliberate constraint going forward: **do not add raw prompt/response text fields to `StepEvent` or any tracer** without first deciding how PII in that content would be handled (redaction? exclusion? consent?) — don't let it get added silently as "useful for debugging."

## Where PII could still leak, unaddressed
- **Golden datasets** (Evaluation Gate, 04) and **promoted feedback** (Feedback Loop, 11) both carry real `input`/`expected` content by design — a promoted correction is *supposed to* contain real conversation content to be a useful regression case. If that content includes real customer PII, it now lives in a golden-dataset file, version-controlled and reviewed like code (ADR 0006's git-backed model), not specially protected. No policy currently governs whether golden datasets need PII scrubbing before being committed — flagged here, not solved.
- **Data & Tools' retrieval documents** (07) — whatever a client's ingested documents contain is stored as-is in that client's isolated Search index (ADR 0007's isolation is about *cross-client* separation, not PII redaction within a single client's own data).

## Revisit when
- A real usecase's golden dataset or promoted feedback is likely to contain real PII — decide a scrubbing/redaction policy before that data is committed, not after.
- A client contract specifies PII handling requirements beyond what `PIIGuardrail`'s defaults provide — extend the guardrail's category list or thresholds per that usecase's config, following the same config-driven pattern already established.
