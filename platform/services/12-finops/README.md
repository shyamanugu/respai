# FinOps / Cost Management

## What this is
Azure infrastructure cost visibility and budget alerting — a monthly budget with threshold notifications, and a scheduled export of raw Cost Management data to Blob Storage. This is the **infrastructure** cost side (compute, storage, Cognitive Services consumption); it is deliberately separate from Observability (05)'s **LLM usage** cost tracking (`compute_cost()`, `StepEvent.cost_usd`), which reads Model Management's `pricing.yaml` and reflects per-model-call token cost, not Azure resource billing. See "The two kinds of cost" below and `docs/decisions/0014-finops-scope.md`.

## No Python package
Like CI/CD (09), this component's deliverable is Bicep and a reference query, not application code — there's no `src/`, no `pytest.ini`. Nothing here is unit-testable in the way a resolver or a guardrail is; what can be verified is that the Bicep compiles (`az bicep build`), which is checked in CI alongside every other template in this platform.

## The two kinds of cost
| | This component (FinOps) | Observability (05) |
|---|---|---|
| Tracks | Azure resource billing (compute, storage, Cognitive Services meters) | LLM token usage cost per model call |
| Source | Azure Cost Management (subscription/RG-level) | Model Management's `pricing.yaml` |
| Granularity | Daily, per resource | Per `StepEvent` |
| Mechanism | `Microsoft.Consumption/budgets` + `Microsoft.CostManagement/exports` | `compute_cost()` |

Both matter, neither replaces the other — infrastructure cost includes things with no per-token price (a Container Apps environment sitting idle, an Azure AI Search Basic-tier fixed cost), while LLM usage cost is invisible to Cost Management until the bill settles days later. Correlating the two (e.g., "this usecase's total cost, infra + tokens") is a real future need, not built now — see "Revisit When."

## Whether this can actually be deployed under Contributor: unconfirmed
Budget creation (`Microsoft.Consumption/budgets/write`) is sometimes gated differently depending on the billing account model (EA, MCA, pay-as-you-go) in ways that don't always track cleanly with Contributor vs. Owner. This is exactly the open question Phase 0's access audit already flagged (`docs/checklist/BUILD-CHECKLIST.md`, "confirm Cost Management Contributor is included in current access"). `budget.bicep` is authored and ready to deploy — attempting the deployment is itself the way to resolve the uncertainty; if it fails with an authorization error, that becomes a new, concrete Phase 0 queue item rather than a guess made in advance.

## Cost export destination
`cost-export.bicep` writes to a Blob Storage container via `exportStorageAccountId` — a parameter, not a new Storage Account provisioned by this component. Reuse Feedback Loop (11)'s storage account (or any existing one) rather than provisioning a redundant account solely for this.

## The Workbook is deliberately not built
A cost-by-tag dashboard would normally be an Azure Workbook (`Microsoft.Insights/workbooks`), but a Workbook's `serializedData` is a deeply nested, version-sensitive JSON blob — authoring one with real confidence, the way `budget.bicep` and `cost-export.bicep` could be, isn't something this could responsibly guess at without verifying against a live Workbook first. Instead, `queries/cost-by-tag.kql` is a plain reference KQL query (by `businessUnit` tag, matching component 01's tagging schema) — explicitly marked as unverified against real exported data, since no export has ever run. See ADR 0009's identical reasoning for not wrapping Content Safety's Prompt Shields.

## File layout
```
infra/
├── budget.bicep                 # Microsoft.Consumption/budgets — monthly amount + 50%/80%/forecasted-100% notifications
├── cost-export.bicep             # Microsoft.CostManagement/exports — daily actual-cost CSV to Blob Storage
└── main.parameters.dev.json

queries/
└── cost-by-tag.kql              # reference query, unverified against real data — see above
```

## Prerequisites
- A Storage Account to export into (e.g. component 11's) — resource ID supplied as a parameter, not provisioned here
- Confirmed billing/budget write access (see "Whether this can actually be deployed" above) — not confirmed yet

## Setup (once ready to attempt)
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/budget.bicep \
  --parameters infra/main.parameters.dev.json notificationEmails='["real-email@afni.com"]'

az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/cost-export.bicep \
  --parameters infra/main.parameters.dev.json exportStorageAccountId=<component 11's storage account resource ID>
```
Nothing above has been run yet in this build.

## Dependencies
- Depends on: an existing Storage Account (e.g. component 11) for the export destination
- Depended on by: nothing yet — this is visibility tooling, not something other components call

## Cost notes
Budgets and alerts themselves are free. Cost Management exports are free (the export mechanism itself has no charge; you're paying for the Storage Account holding the exported files, at standard Blob pricing). Nothing is provisioned yet.
