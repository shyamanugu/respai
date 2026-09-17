# Repository & Foundation

## What this is
The base layer every other component builds on: resource naming, tagging, and a Managed Identity that will eventually carry every other component's Azure permissions. Nothing usecase-specific lives here — this is pure platform scaffolding.

## Azure resources used
- **Resource Group** — the deployment boundary for this environment
- **User-Assigned Managed Identity** — created now, permissions attached later (see Configuration)

Key Vault is intentionally not part of this component yet. See `docs/decisions/0001-repo-foundation-approach.md` for why.

## Naming convention
Pattern: `<type-abbreviation>-<workload>-<environment>-<region>-<instance>`

| Resource type | Abbreviation | Example |
|---|---|---|
| Resource Group | `rg` | `rg-llmops-dev-eastus-001` |
| User-Assigned Managed Identity | `id` | `id-llmops-dev-eastus-001` |
| Key Vault (future) | `kv` | `kv-llmops-dev-eastus-001` |

`workload` stays `llmops` across every component. `environment` is `dev`, `test`, or `prod`. `instance` only increments if more than one deployment of the same resource type coexists in the same environment.

## Tagging schema
Every resource in this platform carries:

| Tag | Purpose |
|---|---|
| `environment` | dev / test / prod |
| `project` | `llmops` |
| `owner` | Accountable team or individual |
| `costCenter` | For billing allocation |
| `businessUnit` | Reserved for per-client/program cost separation once multiple BPO engagements are onboarded |

## Prerequisites
- Azure CLI installed and authenticated (`az login`)
- Bicep CLI available (bundled with recent Azure CLI versions)
- Contributor role on the target subscription or resource group

## Setup

**If Contributor scope includes Resource Group creation (subscription-level):**
```bash
az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.dev.json
```

**If Contributor scope is fixed to an existing Resource Group:**
Request the Resource Group be created using the naming convention above, then deploy only the identity:
```bash
az deployment group create \
  --resource-group rg-llmops-dev-eastus-001 \
  --template-file infra/managed-identity.bicep \
  --parameters environmentName=dev location=eastus workloadName=llmops instance=001
```

## Configuration
Copy the placeholder values in the root `.env` and adjust for your environment. These are not secrets — no live credential belongs in this file, now or later. Once Key Vault is available, this file is replaced by a config loader that reads from it instead; until then, every component reads these same values directly.

## Local development
No running service here — this component is infrastructure and configuration only. Validate the Bicep templates locally before deploying:
```bash
az bicep build --file infra/main.bicep
```

## Deployment
Deployed manually for now via the Azure CLI commands above. Once CI/CD (component 09) is live, this becomes a pipeline step gated on a manual approval, since it provisions the environment every other component depends on.

## Dependencies
None — this is the first component. Every other component depends on the Resource Group and Managed Identity produced here.

## Cost notes
Both resources are free. A Resource Group has no cost by itself, and a Managed Identity has no associated charge.
