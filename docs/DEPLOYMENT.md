# Deployment Guide — Azure Container Apps (Contributor-only, no Key Vault)

A follow-along guide to deploy APIX + the LLMOps ops console to **Azure Container
Apps**, tuned to these constraints:

- You have **Contributor** on an existing resource group (can create resources,
  **cannot create role assignments** or use Key Vault RBAC).
- **No Key Vault** for now → secrets live as **Container App secrets** (encrypted
  at rest), and data access uses **connection strings / SQL username+password**
  (not managed identity).
- **Images in GitHub Container Registry (ghcr.io)**, built by GitHub Actions
  (no local Docker needed).
- **Least cost** → Consumption plan, apps **scale to zero** when idle.

> Work through this top to bottom. When you hit a snag, tell me the step # and the
> error and I'll refine this doc.

---

## 0. Naming conventions (use these everywhere)

Aligned to the Azure Cloud Adoption Framework (`<type>-<workload>-<env>`), lower-case,
`dev`/`prod` for env. **Workload token = `apix`.**

| Thing | Suggested name | Notes |
|---|---|---|
| **Client git repo** | `afni-llmops-platform` | or `afni-genai-platform`. Describes it as the platform, not one app. Keep this monorepo layout. |
| Resource group | `rg-apix-dev` | (you already have one — reuse it; the doc assumes `rg-apix-dev`) |
| Container Apps Environment | `cae-apix-dev` | the shared cost unit; create once |
| Log Analytics workspace | `log-apix-dev` | |
| Storage account | `stapixdev` | 3–24 chars, lowercase, **no dashes**, globally unique — add digits if taken (`stapixdev01`) |
| Azure Files share | `llmops-data` | the shared trace/feedback sink mounted at `/data` |
| Container App — APIX dashboard | `ca-apix-dev` | API (+ React UI) |
| Container App — APIX chatbot | `ca-apix-chatbot-dev` | |
| Container App — LLMOps ops console | `ca-llmops-dev` | ops backend (+ React UI) |
| Container Apps Job — pipeline | `caj-apix-pipeline-dev` | scheduled/manual batch |
| ghcr images | `ghcr.io/shyamanugu/apix-<component>` | e.g. `apix-chatbot`, `apix-ops-backend` |

General resource-name pattern: **`<abbr>-apix-<env>`** (Container App `ca-`, env
`cae-`, job `caj-`, log `log-`); storage is the exception (`st` + `apix` + `env`,
no dashes). For prod, swap `dev`→`prod`.

---

## 1. Prerequisites

```bash
az login
az account set --subscription "<your-subscription>"
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```
Set variables you'll reuse:
```bash
RG=rg-apix-dev
LOC=eastus                 # your region
ENVN=cae-apix-dev
OWNER=shyamanugu           # ghcr owner (lowercase)
```

---

## Part A — Build & push images to ghcr.io

1. Push this repo to GitHub (done: `github.com/shyamanugu/respai`) or your client
   repo. Ensure `.github/workflows/build-and-push.yml` is present.
2. GitHub → **Actions → build-and-push → Run workflow** (branch `main`). It builds
   all six images and pushes `ghcr.io/<owner>/apix-*:latest` (+ the commit SHA).
3. GitHub → your profile → **Packages**: for each `apix-*` package, open
   **Package settings**. Either:
   - make it **Private** and create a **classic PAT** with `read:packages`
     (Settings → Developer settings → Tokens) — Azure will use this to pull; **or**
   - make it **Public** (simplest; no pull credential needed — fine for
     non-sensitive images, though this repo's images bundle app code).

> You only rebuild images when code changes. Re-run the workflow to publish a new
> `latest`.

---

## Part B — Create the shared Azure resources (once)

```bash
# Log Analytics (for Container Apps logs)
az monitor log-analytics workspace create -g $RG -n log-apix-dev -l $LOC
LAW_ID=$(az monitor log-analytics workspace show -g $RG -n log-apix-dev --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n log-apix-dev --query primarySharedKey -o tsv)

# Container Apps Environment (the cost unit; Consumption)
az containerapp env create -g $RG -n $ENVN -l $LOC \
  --logs-workspace-id $LAW_ID --logs-workspace-key $LAW_KEY

# Storage account + Azure Files share for the shared /data sink
az storage account create -g $RG -n stapixdev -l $LOC --sku Standard_LRS --kind StorageV2
STKEY=$(az storage account keys list -g $RG -n stapixdev --query "[0].value" -o tsv)
az storage share-rm create -g $RG --storage-account stapixdev -n llmops-data --quota 5

# Register the file share with the environment so apps can mount it
az containerapp env storage set -g $RG -n $ENVN \
  --storage-name llmops-data --azure-file-account-name stapixdev \
  --azure-file-account-key $STKEY --azure-file-share-name llmops-data --access-mode ReadWrite
```

---

## Part C — Deploy the Container Apps

Each app pulls its image from ghcr. If your packages are **private**, first create
the registry credential on each app with:
```bash
# run once per app after creation (replace <app>):
az containerapp registry set -g $RG -n <app> \
  --server ghcr.io --username $OWNER --password <YOUR_READ_PACKAGES_PAT>
```

### C.1 Secrets (per app that needs them)
Set secrets once, then reference them in env vars as `secretref:<name>`:
```bash
az containerapp secret set -g $RG -n <app> --secrets \
  reasoning-api-key=<...> \
  blob-conn='<AZURE_BLOB_CONNECTION_STRING>' \
  sql-password=<...> \
  chat-jwt-secret=<...> \
  session-secret=<...>
```

### C.2 LLMOps ops console — `ca-llmops-dev`
```bash
az containerapp create -g $RG -n ca-llmops-dev --environment $ENVN \
  --image ghcr.io/$OWNER/apix-ops-backend:latest \
  --ingress external --target-port 8100 \
  --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=dev LLMOPS_TRACER=jsonl \
    LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    APIX_FEEDBACK_PATH=/data/feedback/feedback.jsonl \
    APIX_EVAL_HISTORY_PATH=/data/eval/eval_runs.jsonl \
    OPS_DB_PATH=/data/ops/ops.db
# mount the shared /data volume
az containerapp update -g $RG -n ca-llmops-dev \
  --set-env-vars OPS_CORS_ORIGINS='*' \
  # volume mount is set via the YAML update below (see Note on volumes)
```

### C.3 APIX chatbot — `ca-apix-chatbot-dev`
```bash
az containerapp create -g $RG -n ca-apix-chatbot-dev --environment $ENVN \
  --image ghcr.io/$OWNER/apix-chatbot:latest \
  --ingress external --target-port 8000 \
  --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=dev LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    REASONING_MODEL_ENDPOINT=<...> REASONING_MODEL_DEPLOYMENT=<...> \
    AZURE_SQL_SERVER=<...> AZURE_SQL_DATABASE=<...> \
    REASONING_MODEL_APIKEY=secretref:reasoning-api-key \
    AZURE_SQL_PASSWORD=secretref:sql-password \
    CHAT_JWT_SECRET=secretref:chat-jwt-secret \
    AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn
```

### C.4 APIX dashboard — `ca-apix-dev`
The current build has the dashboard API and React UI as **two images**. Two options:
- **Now (no code change):** deploy `apix-dashboard-api` (internal) **and**
  `apix-dashboard-web` (external, nginx) as two apps — 4 apps total. Set the
  web app's `DASHBOARD_API_UPSTREAM` to the api app's internal URL.
- **Recommended (I make a small change):** I mount the built SPA as static files
  inside the dashboard FastAPI so it's **one image / one app** `ca-apix-dev`
  serving both UI and `/api`. Tell me and I'll change it, then this becomes a
  single `az containerapp create` like C.3 on port 8000.

```bash
# api (internal)
az containerapp create -g $RG -n ca-apix-api-dev --environment $ENVN \
  --image ghcr.io/$OWNER/apix-dashboard-api:latest \
  --ingress internal --target-port 8000 --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --env-vars APIX_ENV=dev LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
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
# web (external, nginx → proxies /api to the api app)
az containerapp create -g $RG -n ca-apix-web-dev --environment $ENVN \
  --image ghcr.io/$OWNER/apix-dashboard-web:latest \
  --ingress external --target-port 80 --min-replicas 0 --max-replicas 2 --cpu 0.25 --memory 0.5Gi \
  --env-vars DASHBOARD_API_UPSTREAM=https://<ca-apix-api-dev-internal-fqdn>
```

### Note on mounting the `/data` volume
`az containerapp create` can't attach an env storage inline in older CLIs. After
creating an app that needs `/data` (ops backend, chatbot, dashboard-api), export,
edit, and re-apply its YAML:
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

## Part D — Run the pipeline (batch)

Create it as a **manual/scheduled Job** (least cost — runs only when triggered):
```bash
az containerapp job create -g $RG -n caj-apix-pipeline-dev --environment $ENVN \
  --trigger-type Manual --replica-timeout 3600 --replica-retry-limit 1 \
  --image ghcr.io/$OWNER/apix-pipeline:latest --cpu 1.0 --memory 2.0Gi \
  --env-vars APIX_ENV=dev LLMOPS_TRACER=jsonl LLMOPS_TRACE_FILE=/data/traces/trace.jsonl \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    SALES_STORAGE_ACCOUNT_NAME=<...> SALES_STORAGE_ACCOUNT_KEY=secretref:... \
    REASONING_MODEL_ENDPOINT=<...> REASONING_MODEL_DEPLOYMENT=<...> \
    REASONING_MODEL_APIKEY=secretref:reasoning-api-key
# add the /data volume via the YAML method above, then start a run:
az containerapp job start -g $RG -n caj-apix-pipeline-dev \
  --args "--mode" "telesales" "--date" "2025-08-28"
```
Attach the same `llmops-data` volume so its traces show up in the ops console.

---

## Part E — Wire it together & verify

**Values that MUST match across apps** (or the data contract / chat auth break):
`REASONING_MODEL_*`, `AZURE_BLOB_*`, the Azure SQL server/db, and `CHAT_JWT_SECRET`.

1. Get each app's URL: `az containerapp show -g $RG -n <app> --query properties.configuration.ingress.fqdn -o tsv`.
2. Set the dashboard's `CHAT_API_BASE_URL` to the chatbot's FQDN, and (for SSO
   later) register `https://<dashboard>/api/auth/callback` in the Entra app.
3. Verify:
   - `https://<ca-llmops-dev>/healthz` → ops backend OK.
   - `https://<ca-apix-chatbot-dev>/health` → chatbot OK.
   - Run the pipeline job → open the ops console → traces/cost appear.

---

## Part F — Cost controls (least resources)

- `--min-replicas 0` on every app → **scale to zero**; you pay compute only while
  requests are in flight. Idle cost ≈ Storage + Log Analytics (both minimal).
- Small sizes: `--cpu 0.25–0.5 --memory 0.5–1.0Gi`.
- One shared Environment (`cae-apix-dev`) for all apps.
- Pipeline as a **Job**, not an always-on app.
- Set Log Analytics daily cap if desired: `az monitor log-analytics workspace update -g $RG -n log-apix-dev --quota 1` (1 GB/day).

---

## Part G — GitHub Actions CD (later)

Once manual deploy works, add a deploy job that runs after `build-and-push`:
```yaml
# .github/workflows/deploy.yml (sketch)
- uses: azure/login@v2
  with: { creds: ${{ secrets.AZURE_CREDENTIALS }} }   # a service principal JSON
- run: az containerapp update -g rg-apix-dev -n ca-apix-chatbot-dev \
        --image ghcr.io/${{ github.repository_owner }}/apix-chatbot:${{ github.sha }}
```
`AZURE_CREDENTIALS` needs a service principal with Contributor on the RG — ask
whoever owns the subscription to create it (`az ad sp create-for-rbac`), since it
needs a role assignment you may not have.

---

## Secrets checklist (Container App secrets, no Key Vault)

| Secret | Used by |
|---|---|
| `reasoning-api-key` (`REASONING_MODEL_APIKEY`) | all apps + pipeline |
| `blob-conn` (`AZURE_BLOB_CONNECTION_STRING`) | dashboard, chatbot |
| `sql-password` (`AZURE_SQL_PASSWORD`) | dashboard, chatbot |
| `chat-jwt-secret` (`CHAT_JWT_SECRET`) | dashboard + chatbot (**must match**) |
| `session-secret` (`APIX_SESSION_SECRET`) | dashboard |
| storage account key (`SALES_STORAGE_ACCOUNT_KEY`) | pipeline |
| ghcr PAT | registry pull (if packages are private) |

Migrate these to Key Vault + managed identity once you have `User Access
Administrator` (or ask the subscription owner to grant the role assignments).
