# Costing — dev/demo environment

**Bottom line: this stack is designed to cost close to $0/month when idle, and
only a few dollars when you actually run a demo.** Every Container App scales to
zero (`--min-replicas 0`) — there is no always-on compute anywhere. The two real
variable costs are (1) how much data you copy into storage, and (2) how many
Azure OpenAI tokens you actually consume. Everything below is pay-as-you-go —
nothing here is a reserved/provisioned SKU.

> Numbers below are **rough public-pricing order-of-magnitude estimates**, not
> quotes — actual rates vary by region and change over time. For exact numbers,
> use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/)
> or check **Cost Management + Billing** on `rg-llmops-apix` after your first
> week of real usage.

## What this infra creates, and what drives its cost

| Resource | Pricing model | Cost driver | Typical dev/demo cost |
|---|---|---|---|
| Container Apps — **2 apps** (`ca-llmops-apix-<tier>` combined + `ca-llmops-<tier>` ops console), optionally +1 job | Consumption — pay per vCPU-second + GiB-second **only while a replica is actively handling a request**; zero while idle | How much real traffic/demo time | Likely **$0** — Azure grants ~180,000 vCPU-seconds + 360,000 GiB-seconds + 2M requests **free per month per subscription**; a demo environment rarely exceeds that |
| Log Analytics (`log-llmops-apix-<tier>`) | Pay per GB ingested, no fixed fee | App log volume | Capped at 1 GB/day by this setup (see `azure-setup.ps1` Part 1) — a few dollars/month at most for light demo logging |
| Storage account (`stllmopsapix<tier>`) | Pay per GB stored + per transaction (Blob + Files, Standard LRS) | **How much real transcript/report data you copy in** — this is the one cost that scales with your actual dataset size, not with demo usage | Cents to a few dollars/month unless the copied dataset is large — check your source data's size |
| Azure OpenAI (`oai-llmops-apix-<tier>`, `gpt-4o-mini`) | Pay per token (input + output), no fixed fee | How many LLM calls you make (chat questions, pipeline analysis runs) | Typically a few dollars/month for light demo/dev use; `gpt-4o-mini` is one of the cheapest current models |
| ghcr.io images | Free for public packages; small free allowance then per-GB for private | Image size × pull frequency | Slightly higher than before — the combined image bundles 3 apps' dependencies (polars/pyarrow/langgraph/streamlit-adjacent) — but still effectively $0 at demo pull frequency |
| GitHub Actions (`build-and-push`, manual only) | 2,000 free minutes/month on GitHub Free for private repos; unlimited on public repos | How often you rebuild images | $0 — you only run it when code changes |
| Azure SQL | **Not created by this infra** — you're reusing an existing database | N/A | Out of scope; already being paid for by whoever owns that database |

## Cost levers already applied

- **Scale to zero everywhere** (`--min-replicas 0`) — the single biggest lever;
  no compute is billed between demos.
- **Fewer, combined apps** — `ai_pipeline` + `application` + `chatbot` now
  deploy as ONE Container App (`ca-llmops-apix-<tier>`) instead of four,
  directly cutting the per-app baseline (each app has a tiny always-provisioned
  footprint even at zero replicas — fewer apps means less of that). The
  combined app is sized **`--cpu 1.0 --memory 2.0Gi`** (up from the individual
  apps' `0.25/0.5Gi`) since it runs 3 uvicorn processes + nginx concurrently —
  still minimum-viable for what it now does, not padding.
- **Minimum Consumption tier otherwise** — the ops console stays at
  `0.25/0.5Gi`; the optional scheduled pipeline job at `0.5/1.0Gi`.
- **`max-replicas 1`** for `dev` — no accidental scale-out cost. (Raise this for
  `qa`/`prod` if you need resilience later.)
- **Log Analytics capped at 1 GB/day** — the one Azure cost with no natural
  ceiling otherwise.
- **Standard_LRS storage** — cheapest redundancy tier; fine for dev/demo data
  that isn't the system of record.
- **ghcr.io instead of ACR** — avoids a registry resource entirely (and the
  role-assignment problem ACR's managed-identity pull would hit under
  Contributor-only — see `DEPLOYMENT.md`).
- **Pipeline as a Job, not an app** — billed only for the seconds it actually runs.

## Do you need to delete resources between demos?

**No.** Because everything already scales to zero, there's nothing extra to
"turn off" — idle cost is already close to zero without any manual teardown. If
you want to be extra sure (e.g. before a long gap with no planned demos), the
only resources that cost anything while fully idle are Storage (cents/month for
whatever's stored) and Log Analytics (only if something is actively logging,
which it isn't when nothing's running) — neither is worth tearing down and
recreating for the setup hassle it'd cost you.

If you ever do want a full teardown: `az group delete -n rg-llmops-apix` removes
everything in this doc in one step — but note it also deletes the storage
account (and whatever real data you copied into it), so don't do this if you
want to keep that data.

## Where the real budget will go

Once this moves past demo/dev, the dominant costs will be **Azure OpenAI token
usage at production traffic volume** and **whatever Azure SQL tier the shared
database runs on** — neither is something this infra controls. Revisit this doc
once you have `qa`/`prod` traffic patterns to estimate against.
