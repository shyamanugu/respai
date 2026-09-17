# Security & Compliance

## What this is
The consolidation point for security and compliance concerns already decided elsewhere in this platform, plus two genuinely new pieces: a validated diagnostic-settings pattern (wiring resource logs to Log Analytics) and two policy documents (data residency, PII handling) that surface real gaps rather than papering over them with vague statements of intent.

## What this component does not decide on its own
Key Vault's deferral (ADR 0001), PII detection's mechanism (Guardrails, ADR 0009), and the RBAC access model (Contributor-only, throughout) were all decided by earlier components. This component's job is to cross-reference them into one place and confirm nothing is silently inconsistent — not to re-litigate them.

## Key Vault access model
Decided in ADR 0001: deferred entirely. No Key Vault resource exists; `.env` (placeholder, committed) / `.env.local` (real values, gitignored) is the interim pattern every component follows. Revisit when Key Vault access (Access Policy grant or RBAC role assignment) is approved — see ADR 0001's own "Revisit When."

## RBAC requests: cross-referenced, none silently pending
`docs/architecture/permissions-log.md` is the complete list — 14 items as of this component, including two found during this component's own audit that weren't previously logged: Azure AI Foundry project role assignment (distinct from GitHub OIDC, relevant only if Prompt Management migrates off git-backed storage per ADR 0006) and the Entra ID app registration Serving & Hosting's (10) HTTP auth would need (the same underlying access as CI/CD's OIDC login, per ADR 0013). Every item remains "not yet requested," consistent with the standing batch-later workflow.

## Data residency
See `data-residency.md`. Short version: `eastus` is a default, not a guarantee; no client has specified a residency requirement yet, so no override mechanism exists. Flagged, not solved.

## PII handling
See `pii-handling-policy.md`. Short version: `PIIGuardrail` (06) is the enforcement mechanism; this document is the policy layer, including a genuine finding — Observability's `StepEvent` doesn't capture raw prompt/response content, which happened to avoid a PII-in-telemetry risk as a side effect of its original design, now documented as a deliberate constraint rather than an accidental gap.

## Diagnostic settings
`infra/diagnostic-settings-example.bicep` is a validated, concrete pattern for wiring one resource's logs/metrics to Log Analytics (component 05) — worked against Azure OpenAI (03) specifically, because a diagnostic setting is an extension resource that must reference its target's exact type via an `existing` block, making one fully generic module across arbitrary resource types something to author with confidence, not guess at. Every other component's Bicep should add its own `diagnosticSettings` child resource following this exact pattern, not a shared abstraction — see `docs/decisions/0016-security-compliance-scope.md` for why.

**Not yet done**: no component's Bicep has actually had a `diagnosticSettings` resource added to it. This component validates and documents the pattern; wiring it into each of the ~10 existing Bicep templates is real, mechanical follow-up work, not done here to avoid re-touching every component's infra file in the same change that established the pattern.

## File layout
```
data-residency.md
pii-handling-policy.md
infra/
└── diagnostic-settings-example.bicep   # validated worked example, Azure OpenAI account
```

## Prerequisites
Component 05 (Observability)'s Log Analytics workspace, once deployed, as the destination for any diagnostic setting following this pattern.

## Dependencies
- Depends on: component 05 (Log Analytics workspace as destination), every other component (whose Bicep would each add its own diagnostic setting)
- Depended on by: nothing yet

## Cost notes
Diagnostic settings themselves are free; Log Analytics ingestion cost (already noted in component 05's README) applies to whatever volume actually gets sent once these are wired in.
