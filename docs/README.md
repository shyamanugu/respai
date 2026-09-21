# docs/

Cross-cutting documentation for the platform and the APIX usecase.

| Doc | What it covers |
|---|---|
| [`RUNBOOK.md`](RUNBOOK.md) | How to configure, run locally (Docker + dev), and onboard another usecase. **Start here.** |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Follow-along **Azure Container Apps** deployment tuned for Contributor-only access (no Key Vault, ghcr.io images, managed-identity SQL, scale-to-zero). Naming conventions + points at [`infra/azure-setup.ps1`](../infra/azure-setup.ps1) for the runnable commands. |
| [`COSTING.md`](COSTING.md) | What each resource costs (pay-as-you-go, lowest tier), the cost levers already applied, and why idle cost is close to $0. |

Component-level docs live next to the code:
- Platform overview + consumption: [`../platform/README.md`](../platform/README.md)
- Onboarding contract + usecase config: [`../usecases/README.md`](../usecases/README.md), [`../usecases/apix/config/README.md`](../usecases/apix/config/README.md)
- Deployment: [`../infra/README.md`](../infra/README.md)
- Monitoring: [`../ops-console/README.md`](../ops-console/README.md)
