# docs/

Cross-cutting documentation for the platform and the APIX usecase.

| Doc | What it covers |
|---|---|
| [`RUNBOOK.md`](RUNBOOK.md) | How to configure, run locally (Docker + dev), and onboard another usecase. **Start here.** |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Follow-along **Azure Container Apps** deployment tuned for Contributor-only access (no Key Vault, ghcr.io images, scale-to-zero). Naming conventions + manual `az` steps. |

Component-level docs live next to the code:
- Platform overview + consumption: [`../platform/README.md`](../platform/README.md)
- Onboarding contract + usecase config: [`../usecases/README.md`](../usecases/README.md), [`../usecases/apix/config/README.md`](../usecases/apix/config/README.md)
- Deployment: [`../infra/README.md`](../infra/README.md)
- Monitoring: [`../ops-console/README.md`](../ops-console/README.md)
