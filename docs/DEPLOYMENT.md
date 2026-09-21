# Deployment Guide — Azure Container Apps (Contributor-only, no Key Vault)

A follow-along guide to deploy APIX + the LLMOps ops console to **Azure Container
Apps**, tuned to these constraints:

- You have **Contributor** on one resource group (`rg-llmops-apix`) — can create
  resources, **cannot create role assignments**, no Key Vault RBAC, no service
  principal creation for CI.
- **dev now, qa/prod later, all inside that one resource group** — each tier gets
  its **own Container Apps Environment** (own Log Analytics + Storage) for full
  isolation; only the resource group is shared.
- **No Key Vault** → secrets live as **Container App secrets** (encrypted at
  rest); data access uses **connection strings / SQL username+password**.
- **Images in GitHub Container Registry (ghcr.io)**, built **manually** from the
  Actions tab (no local Docker, no auto-build-on-push).
- **Deploy is manual `az` CLI only, for now.** GitHub Actions CD is deliberately
  *not* set up — it needs a one-time favor from your Azure AD admin (see Part G)
  that you don't have to chase yet.
- **Least cost** → Consumption plan, every app **scales to zero** when idle.

> **Order of operations:** (1) get the app running **locally** first (see
> [`RUNBOOK.md`](RUNBOOK.md)) — that proves your `.env` values are correct before
> you touch Azure. (2) Create the Azure resources below for the `dev` tier only.
> (3) Deploy the Container Apps. Add `qa`/`prod` later by repeating Parts B–D with
> `TIER=qa`/`TIER=prod` — same resource group, new environment.
>
> Work through this top to bottom. When you hit a snag, tell me the **Part letter
> + step** and the exact error and I'll refine this doc.

---

## 0. Naming conventions (use these everywhere)

**One resource group for every tier:** `rg-llmops-apix`. Everything inside it is
suffixed with the tier (`dev` / `qa` / `prod`) so dev, qa, and prod resources never
collide even though they share the group.

| Thing | Pattern | Example (`dev`) |
|---|---|---|
| Resource group (shared, all tiers) | `rg-llmops-apix` | `rg-llmops-apix` |
| Container Apps Environment (**one per tier**) | `cae-apix-<tier>` | `cae-apix-dev` |
| Log Analytics workspace (**one per tier**) | `log-apix-<tier>` | `log-apix-dev` |
| Storage account (**one per tier**, no dashes, globally unique) | `stapix<tier>` | `stapixdev` (append digits if taken: `stapixdev01`) |
| Azure Files share (same literal name in every storage account — no clash, different accounts) | `llmops-data` | `llmops-data` |
| Container App — APIX dashboard | `ca-apix-<tier>` | `ca-apix-dev` |
| Container App — APIX chatbot | `ca-apix-chatbot-<tier>` | `ca-apix-chatbot-dev` |
| Container App — LLMOps ops console | `ca-llmops-<tier>` | `ca-llmops-dev` |
| Container Apps Job — pipeline batch | `caj-apix-pipeline-<tier>` | `caj-apix-pipeline-dev` |
| ghcr images (**not** per tier — same image, different env vars per tier) | `ghcr.io/<owner>/apix-<component>` | `ghcr.io/shyamanugu/apix-chatbot` |
| Client git repo | `afni-llmops-platform` | (suggestion — describes it as the platform, not one app) |

General pattern: **`<abbr>-apix-<tier>`** — Container App `ca-`, environment
`cae-`, job `caj-`, log `log-`. Storage is the exception (`st` + `apix` + tier,
no dashes). Images are shared across tiers; only the *deployed tag* and the
*environment variables* differ per tier (see Part F, "promoting a build").

---

## 1. Prerequisites

```bash
az login
az account set --subscription "<your-subscription>"
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

### Variables block — set once per tier, then run everything below unchanged
This is the only thing that changes when you later add `qa` or `prod`: change
`TIER` and re-run Parts B–D.
```bash
RG=rg-llmops-apix          # shared resource group — create once (Part 1b), reused by every tier
TIER=dev                  # dev | qa | prod
LOC=eastus                 # your region
OWNER=shyamanugu           # ghcr owner (lowercase)

CAE=cae-apix-$TIER
LAW=log-apix-$TIER
ST=stapix$TIER            # if this name is taken globally, append digits, e.g. stapixdev01
SHARE=llmops-data
```

### 1b. Create the resource group (once — shared by all tiers)
Skip if `rg-llmops-apix` already exists.
```bash
az group create -n $RG -l $LOC
```

---

## Part A — Build & push images to ghcr.io (once per code change, not per tier)

1. Push this repo to GitHub (already done: `github.com/shyamanugu/respai`) or your
   client repo, once transferred (see the separate transfer notes I gave you).
2. GitHub → **Actions → build-and-push → Run workflow** (branch `main`). It's
   **manual only** — nothing builds automatically on push. It builds all six
   images and pushes `ghcr.io/<owner>/apix-*:latest` (+ the commit SHA).
3. GitHub → your profile → **Packages**: for each `apix-*` package, open
   **Package settings**. Either:
   - make it **Private** and create a **classic PAT** with `read:packages`
     (Settings → Developer settings → Tokens) — Azure will use this to pull; **or**
   - make it **Public** (simplest; no pull credential needed).

Images are **not** rebuilt per tier — you build once, then deploy the same tag to
`dev`, and later *promote* the same tag to `qa`/`prod` once it's verified (see
Part F).

---

## Part B — Create this tier's Azure resources (repeat per tier)

Uses the variables block above. Run once with `TIER=dev`; repeat later with
`TIER=qa` / `TIER=prod` — same resource group, brand-new environment each time
(full isolation between tiers).

```bash
# Log Analytics for this tier
az monitor log-analytics workspace create -g $RG -n $LAW -l $LOC
LAW_ID=$(az monitor log-analytics workspace show -g $RG -n $LAW --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n $LAW --query primarySharedKey -o tsv)

# Container Apps Environment for this tier (Consumption — scale to zero)
az containerapp env create -g $RG -n $CAE -l $LOC \
  --logs-workspace-id $LAW_ID --logs-workspace-key $LAW_KEY

# Storage account + Azure Files share for this tier's shared /data sink
az storage account create -g $RG -n $ST -l $LOC --sku Standard_LRS --kind StorageV2
STKEY=$(az storage account keys list -g $RG -n $ST --query "[0].value" -o tsv)
az storage share-rm create -g $RG --storage-account $ST -n $SHARE --quota 5

# Register the file share with this tier's environment so its apps can mount it
az containerapp env storage set -g $RG -n $CAE \
  --storage-name $SHARE --azure-file-account-name $ST \
  --azure-file-account-key $STKEY --azure-file-share-name $SHARE --access-mode ReadWrite
```

Optional cost cap for this tier's logs:
```bash
az monitor log-analytics workspace update -g $RG -n $LAW --quota 1   # 1 GB/day
```

---

## Part C — Deploy the Container Apps (repeat per tier)

Each app pulls its image from ghcr. If your packages are **private**, create the
registry credential on each app right after creating it:
```bash
az containerapp registry set -g $RG -n <app> \
  --server ghcr.io --username $OWNER --password <YOUR_READ_PACKAGES_PAT>
```

### C.1 Secrets (per app that needs them, per tier)
Set secrets once per app, then reference them in env vars as `secretref:<name>`.
**Use different secret values per tier** (e.g. a dev vs prod Azure OpenAI key) —
the secret *name* can stay the same across tiers since each app instance is
independent.
```bash
az containerapp secret set -g $RG -n <app> --secrets \
  reasoning-api-key=<...> \
  blob-conn='<AZURE_BLOB_CONNECTION_STRING>' \
  sql-password=<...> \
  chat-jwt-secret=<...> \
  session-secret=<...>
```

### C.2 LLMOps ops console — `ca-llmops-$TIER`
```bash
az containerapp create -g $RG -n ca-llmops-$TIER --environment $CAE \
  --image ghcr.io/$OWNER/apix-ops-backend:latest \
  --ingress external --target-port 8100 \
  --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=$TIER LLMOPS_TRACER=jsonl \
    LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    APIX_FEEDBACK_PATH=/data/feedback/feedback.jsonl \
    APIX_EVAL_HISTORY_PATH=/data/eval/eval_runs.jsonl \
    OPS_DB_PATH=/data/ops/ops.db OPS_CORS_ORIGINS='*'
```
Then mount `/data` — see the **Note on volumes** below (every app that reads/writes
traces needs this).

### C.3 APIX chatbot — `ca-apix-chatbot-$TIER`
```bash
az containerapp create -g $RG -n ca-apix-chatbot-$TIER --environment $CAE \
  --image ghcr.io/$OWNER/apix-chatbot:latest \
  --ingress external --target-port 8000 \
  --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=$TIER LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    REASONING_MODEL_ENDPOINT=<...> REASONING_MODEL_DEPLOYMENT=<...> \
    AZURE_SQL_SERVER=<...> AZURE_SQL_DATABASE=<...> \
    REASONING_MODEL_APIKEY=secretref:reasoning-api-key \
    AZURE_SQL_PASSWORD=secretref:sql-password \
    CHAT_JWT_SECRET=secretref:chat-jwt-secret \
    AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn
```

### C.4 APIX dashboard — `ca-apix-$TIER`
The current build has the dashboard API and React UI as **two images**. Two options:
- **Now (no code change):** deploy `apix-dashboard-api` (internal) **and**
  `apix-dashboard-web` (external, nginx) as two apps. Set the web app's
  `DASHBOARD_API_UPSTREAM` to the api app's internal URL.
- **Later (I can make this change):** mount the built SPA as static files inside
  the dashboard FastAPI so it's **one image / one app** — tell me and I'll change
  it, then this collapses into one `az containerapp create` like C.3.

```bash
# api (internal)
az containerapp create -g $RG -n ca-apix-api-$TIER --environment $CAE \
  --image ghcr.io/$OWNER/apix-dashboard-api:latest \
  --ingress internal --target-port 8000 --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=$TIER LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    REASONING_MODEL_ENDPOINT=<...> REASONING_MODEL_DEPLOYMENT=<...> \
    APP_AZURE_SQL_SERVER=<...> APP_AZURE_SQL_DATABASE=<...> \
    CHAT_API_BASE_URL=https://<chatbot-fqdn> \
    APIX_SPA_ORIGINS=https://<dashboard-web-fqdn> \
    REASONING_MODEL_APIKEY=secretref:reasoning-api-key \
    AZURE_SQL_PASSWORD=secretref:sql-password \
    CHAT_JWT_SECRET=secretref:chat-jwt-secret \
    APIX_SESSION_SECRET=secretref:session-secret \
    AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn
# web (external, nginx -> proxies /api to the api app)
az containerapp create -g $RG -n ca-apix-web-$TIER --environment $CAE \
  --image ghcr.io/$OWNER/apix-dashboard-web:latest \
  --ingress external --target-port 80 --min-replicas 0 --max-replicas 2 --cpu 0.25 --memory 0.5Gi \
  --env-vars DASHBOARD_API_UPSTREAM=https://<ca-apix-api-TIER-internal-fqdn>
```

### Note on mounting the `/data` volume
`az containerapp create` can't attach environment storage inline in every CLI
version. After creating an app that needs `/data` (ops console, chatbot,
dashboard-api), export, edit, and re-apply its YAML:
```bash
az containerapp show -g $RG -n <app> -o yaml > app.yaml
# under properties.template add:
#   volumes:
#     - name: data
#       storageType: AzureFile
#       storageName: llmops-data
#   containers[0].volumeMounts:
#     - volumeName: data
#       mountPath: /data
az containerapp update -g $RG -n <app> --yaml app.yaml
```

---

## Part D — Run the pipeline (batch, repeat per tier)

Create it as a **manual Job** (least cost — runs only when triggered):
```bash
az containerapp job create -g $RG -n caj-apix-pipeline-$TIER --environment $CAE \
  --trigger-type Manual --replica-timeout 3600 --replica-retry-limit 1 \
  --image ghcr.io/$OWNER/apix-pipeline:latest --cpu 1.0 --memory 2.0Gi \
  --env-vars APIX_ENV=$TIER LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    SALES_STORAGE_ACCOUNT_NAME=<...> SALES_STORAGE_ACCOUNT_KEY=secretref:... \
    REASONING_MODEL_ENDPOINT=<...> REASONING_MODEL_DEPLOYMENT=<...> \
    REASONING_MODEL_APIKEY=secretref:reasoning-api-key
# add the /data volume via the YAML method above, then start a run:
az containerapp job start -g $RG -n caj-apix-pipeline-$TIER \
  --args "--mode" "telesales" "--date" "2025-08-28"
```
Attach the same tier's `llmops-data` share so its traces show up in that tier's
ops console.

---

## Part E — Wire it together & verify (per tier)

**Values that MUST match across the apps in the same tier** (or the data
contract / chat auth break): `REASONING_MODEL_*`, `AZURE_BLOB_*`, the Azure SQL
server/db, and `CHAT_JWT_SECRET`.

1. Get each app's URL: `az containerapp show -g $RG -n <app> --query properties.configuration.ingress.fqdn -o tsv`.
2. Set the dashboard's `CHAT_API_BASE_URL` to that tier's chatbot FQDN, and (for
   SSO later) register `https://<dashboard>/api/auth/callback` in the Entra app —
   you'll need one redirect URI per tier if dev/qa/prod each have SSO.
3. Verify:
   - `https://<ca-llmops-$TIER>/healthz` → ops backend OK.
   - `https://<ca-apix-chatbot-$TIER>/health` → chatbot OK.
   - Run the pipeline job → open that tier's ops console → traces/cost appear.

---

## Part F — Promoting a build to qa/prod

Images are shared across tiers — you don't rebuild for qa/prod, you **redeploy
the same tag** you already verified in dev:
```bash
az containerapp update -g $RG -n ca-apix-chatbot-qa \
  --image ghcr.io/$OWNER/apix-chatbot:<the-sha-you-verified-in-dev>
```
Keep each tier's `.env` values (endpoints, SQL server, secrets) separate — only
the image tag is promoted, not the config.

---

## Part G — Cost controls (least resources)

- `--min-replicas 0` on every app → **scale to zero**; you pay compute only while
  requests are in flight. Idle cost per tier ≈ its Storage + Log Analytics (both
  minimal) plus the Container Apps Environment (no separate charge on
  Consumption beyond what apps use).
- Small sizes: `--cpu 0.25–0.5 --memory 0.5–1.0Gi`.
- Pipeline as a **Job**, not an always-on app.
- **Don't create `qa`/`prod` until you actually need them** — Part B is designed
  to be run again later with `TIER=qa`; there's no cost for tiers you haven't
  created yet.

---

## Part H — GitHub Actions CD (deferred — needs a one-time admin favor)

**Not set up.** With Contributor-only, you can create resources but you **cannot
create a role assignment** for a new identity (that needs Owner or User Access
Administrator) — and GitHub Actions needs *some* Azure identity to authenticate
as. This isn't a workaround-able limitation; it's how Azure RBAC works.

When you're ready, ask your Azure AD admin to do this **once**:
```bash
# admin runs this (needs Owner/UAA on the RG) — you cannot run it yourself:
az ad sp create-for-rbac --name "sp-apix-cd" --role Contributor \
  --scopes /subscriptions/<sub>/resourceGroups/rg-llmops-apix \
  --sdk-auth
```
That JSON output becomes the `AZURE_CREDENTIALS` GitHub secret. After that, a
`deploy.yml` workflow can run `az containerapp update --image ...` per app,
exactly like the manual commands in Parts C/D, but from CI. Until then, keep
deploying manually — it works fine and needs nothing from anyone else.

---

## Secrets checklist (Container App secrets, no Key Vault)

| Secret | Used by |
|---|---|
| `reasoning-api-key` (`REASONING_MODEL_APIKEY`) | all apps + pipeline |
| `blob-conn` (`AZURE_BLOB_CONNECTION_STRING`) | dashboard, chatbot |
| `sql-password` (`AZURE_SQL_PASSWORD`) | dashboard, chatbot |
| `chat-jwt-secret` (`CHAT_JWT_SECRET`) | dashboard + chatbot (**must match**, per tier) |
| `session-secret` (`APIX_SESSION_SECRET`) | dashboard |
| storage account key (`SALES_STORAGE_ACCOUNT_KEY`) | pipeline |
| ghcr PAT | registry pull (if packages are private) |

Migrate these to Key Vault + managed identity once you have (or a favor from
someone with) `User Access Administrator` on the resource group.
