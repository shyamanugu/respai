# docs/

Cross-cutting documentation for the platform and the APIX usecase.

| Doc | What it covers |
|---|---|
| [`AZURE_PORTAL_SETUP.md`](AZURE_PORTAL_SETUP.md) | **No `az` CLI on the VDI? Start here.** 100% Portal clicks, in priority order: Azure resources for local run first, Container Apps deployment last. Answers the "what image tag do I enter" question directly. |
| [`RUNBOOK.md`](RUNBOOK.md) | How to configure, run locally (Docker + dev), and onboard another usecase. |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Follow-along **Azure Container Apps** deployment via `az` CLI, tuned for Contributor-only access (no Key Vault, ghcr.io images, managed-identity SQL, scale-to-zero). Use this once CLI is available; use `AZURE_PORTAL_SETUP.md` until then. |
| [`COSTING.md`](COSTING.md) | What each resource costs (pay-as-you-go, lowest tier), the cost levers already applied, and why idle cost is close to $0. |

Component-level docs live next to the code:
- Platform overview + consumption: [`../platform/README.md`](../platform/README.md)
- Onboarding contract + usecase config: [`../usecases/README.md`](../usecases/README.md), [`../usecases/apix/config/README.md`](../usecases/apix/config/README.md)
- Deployment: [`../infra/README.md`](../infra/README.md)
- Monitoring: [`../ops-console/README.md`](../ops-console/README.md)
