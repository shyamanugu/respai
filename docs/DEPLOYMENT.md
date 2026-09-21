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
  rest). **Azure SQL access is managed-identity only** — the app code has no
  username/password path (see §2), so this isn't a choice, it's how the code works.
- **Images in GitHub Container Registry (ghcr.io)**, built **manually** from the
  Actions tab (no local Docker, no auto-build-on-push).
- **Deploy is manual — PowerShell + `az` CLI only, for now.** GitHub Actions CD
  is deliberately *not* set up — it needs a one-time favor from your Azure AD
  admin (see Part H) that you don't have to chase yet.
- **Least cost, everywhere** → Consumption plan, minimum CPU/memory, every app
  **scales to zero** when idle. See [`COSTING.md`](COSTING.md) for the full
  breakdown and estimates.
- You're reusing an **existing Azure SQL database** (not creating one) and an
  **existing source Blob** with real transcripts (copied into the new storage,
  not recreated).

> **The primary artifact is [`infra/azure-setup.ps1`](../infra/azure-setup.ps1)**
> — open it in VS Code, fill in the placeholders at the top, and run it
> top-to-bottom (or select-and-F8 one `PART` at a time). This document explains
> *why* each part exists; the script has the exact runnable commands.
>
> **Order of operations:** (1) get the app running **locally** first (see
> [`RUNBOOK.md`](RUNBOOK.md)) to prove your values are correct. (2) Run the
> script's Parts 1–10 for `dev`. (3) Do the two manual steps the script can't
> automate: the SQL grant (Part 11) and the `/data` volume mount (Part 12).
>
> Work through this top to bottom. When you hit a snag, tell me the **Part
> number** and the exact error and I'll refine the script/doc.

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
| Azure OpenAI account (**one per tier**) | `oai-apix-<tier>` | `oai-apix-dev` |
| Azure Files share (LLMOps trace/feedback sink) | `llmops-data` | `llmops-data` |
| Blob containers (pipeline data lake, same storage account) | `raw`, `denoised-transcripts`, `analysis`, `summary`, `coach-hierarchy` | — |
| Container App — APIX dashboard API | `ca-apix-<tier>` (internal: `ca-apix-api-<tier>`) | `ca-apix-api-dev` |
| Container App — APIX dashboard web | `ca-apix-web-<tier>` | `ca-apix-web-dev` |
| Container App — APIX chatbot | `ca-apix-chatbot-<tier>` | `ca-apix-chatbot-dev` |
| Container App — LLMOps ops console | `ca-llmops-<tier>` | `ca-llmops-dev` |
| Container Apps Job — pipeline batch | `caj-apix-pipeline-<tier>` | `caj-apix-pipeline-dev` |
| ghcr images (**not** per tier — same image, different env vars per tier) | `ghcr.io/<owner>/apix-<component>` | `ghcr.io/shyamanugu/apix-chatbot` |
| Client git repo | `afni-llmops-platform` | (suggestion — describes it as the platform, not one app) |
| Azure SQL | **not created here** — your existing server/database | — |

General pattern: **`<abbr>-apix-<tier>`** — Container App `ca-`, environment
`cae-`, job `caj-`, log `log-`, OpenAI `oai-`. Storage is the exception (`st` +
`apix` + tier, no dashes). Images are shared across tiers; only the *deployed
tag* and the *environment variables* differ per tier (see Part F).

---

## 1. Prerequisites

```powershell
az login
az account set --subscription "<your-subscription>"
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.CognitiveServices   # Azure OpenAI
```
Resource group `rg-llmops-apix` is already created — skip that step.

---

## 2. Azure SQL: identity-only, and one hardcoded-name gotcha

Both `chatbot` and `application` connect to Azure SQL exclusively via
`DefaultAzureCredential` (Azure AD token auth) — there is **no username/password
code path at all**. That means:

- Each app that talks to SQL (`chatbot`, `dashboard-api`, and the `pipeline`
  job) needs a **system-assigned managed identity** (the script assigns this —
  it's a property on *your own* resource, not a role assignment on someone
  else's, so Contributor is sufficient).
- That identity then needs a **SQL-side grant** — `CREATE USER ... FROM EXTERNAL
  PROVIDER` — run in SSMS/Azure Data Studio/the Portal query editor. This is a
  **SQL permission**, not an Azure RBAC role assignment, so your Contributor-only
  Azure access doesn't block it; you just need SQL access, which you have. Exact
  commands are in `azure-setup.ps1` Part 11.
- **Gotcha:** you said you'd create your own new table and point things at it via
  `.env`. That works cleanly for the **chatbot** (`REP_TABLE` is a real env var).
  It does **not** work as-is for the **dashboard** —
  `application/backend/services/azure_sql_query.py` has `vzw.rep_pivoted`
  **hardcoded** as a literal string in the SQL text, not read from an env var.
  Tell me your new table name when you have it and I'll make that one-line code
  change; until then the dashboard's metrics page will keep querying the
  original table name.

---

## Part A — Build & push images to ghcr.io (once per code change, not per tier)

> **When the Portal (or `az containerapp create`) asks how to get your image —
> "Azure Container Registry" vs "Docker Hub or other registries" vs building
> from source — pick "Docker Hub or other registries" and point it at
> `ghcr.io`.** Don't use ACR's automatic build/deploy flow or a "build from
> source" option: they typically try to grant the app's identity a role on the
> registry, which needs Owner/User Access Administrator — Contributor can't do
> that. They'd also bypass the Dockerfiles already in this repo (ODBC driver
> install, `platform/` copy, the React multi-stage builds), which a source-based
> auto-build doesn't know about. ghcr avoids the role-assignment step entirely —
> pull auth is a plain username/PAT set as a Container App secret.

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

## Parts B–L — run `infra/azure-setup.ps1`

Everything after image-building is in the script, in dependency order, so
each part has what the next part needs by the time it runs:

| Script Part | What it does |
|---|---|
| 1–2 | Log Analytics (capped at 1 GB/day) + this tier's Container Apps Environment |
| 3 | Storage account: the LLMOps `/data` sink (Azure Files) **and** the pipeline's Blob data-lake containers, in one account |
| 4 | `azcopy sync` your real transcripts (`raw`) and org data (`coach-hierarchy`) from your existing source blob into the new storage — fill in the source SAS URLs yourself; I was intentionally not given those details |
| 5 | *(manual, not scripted)* — create your own new SQL table; note the code-change caveat in §2 above |
| 6 | Azure OpenAI resource + a `gpt-4o-mini` deployment; captures the endpoint/key automatically for the parts below |
| 7 | LLMOps ops console app |
| 8 | Chatbot app (+ managed identity) |
| 9 | Dashboard API (internal, + managed identity) then dashboard web (external), wired to each other's URLs |
| 10 | Pipeline batch job (+ managed identity) |
| 11 | *(manual, run in a SQL client)* — the `CREATE USER ... FROM EXTERNAL PROVIDER` grants for the three identities |
| 12 | *(manual, one YAML edit per app in VS Code)* — mount the shared `/data` volume on ops-console, chatbot, dashboard-api, and the pipeline job |

Run the whole file, or select one `# ===== PART N =====` block in VS Code and
press **F8** to run just that part.

---

## Part E — Verify

**Values that MUST match across the apps in the same tier** (or the data
contract / chat auth breaks): `REASONING_MODEL_*` (the script sets these
identically from the one OpenAI deployment), the Azure SQL server/database, and
`CHAT_JWT_SECRET`.

1. `Invoke-RestMethod https://<ca-llmops-dev-fqdn>/healthz` → ops backend OK.
2. `Invoke-RestMethod https://<ca-apix-chatbot-dev-fqdn>/health` → chatbot OK.
3. Run the pipeline job → open the ops console → traces/cost appear.
4. For SSO later, register `https://<dashboard-web-fqdn>/api/auth/callback` in
   the Entra app registration — one redirect URI per tier if dev/qa/prod each
   get SSO.

---

## Part F — Promoting a build to qa/prod

Images are shared across tiers — you don't rebuild for qa/prod, you **redeploy
the same tag** you already verified in dev:
```powershell
az containerapp update -g rg-llmops-apix -n ca-apix-chatbot-qa `
  --image ghcr.io/shyamanugu/apix-chatbot:<the-sha-you-verified-in-dev>
```
Keep each tier's config separate (endpoints, SQL server, secrets, its own
`oai-apix-<tier>` deployment) — only the image tag is promoted, not the config.
Re-run `azure-setup.ps1` with `$Tier = "qa"` to stand up that tier's resources
first.

---

## Part G — Cost controls

See [`COSTING.md`](COSTING.md) for the full breakdown. Short version: every app
scales to zero, sizes are the minimum Consumption tier, Log Analytics is capped,
and nothing here should cost more than a few dollars a month at demo-level usage
— the only cost that scales with something other than idle-vs-active is the
real transcript data volume you copy into storage.

---

## Part H — GitHub Actions CD (deferred — needs a one-time admin favor)

**Not set up.** With Contributor-only, you can create resources but you **cannot
create a role assignment** for a new identity (that needs Owner or User Access
Administrator) — and GitHub Actions needs *some* Azure identity to authenticate
as. This isn't a workaround-able limitation; it's how Azure RBAC works.

When you're ready, ask your Azure AD admin to do this **once**:
```powershell
# admin runs this (needs Owner/UAA on the RG) — you cannot run it yourself:
az ad sp create-for-rbac --name "sp-apix-cd" --role Contributor `
  --scopes /subscriptions/<sub>/resourceGroups/rg-llmops-apix `
  --sdk-auth
```
That JSON output becomes the `AZURE_CREDENTIALS` GitHub secret. After that, a
`deploy.yml` workflow can run `az containerapp update --image ...` per app,
exactly like the manual commands in the script, but from CI. Until then, keep
deploying manually — it works fine and needs nothing from anyone else.

---

## Secrets checklist (Container App secrets, no Key Vault)

| Secret | Used by |
|---|---|
| `reasoning-api-key` (`REASONING_MODEL_APIKEY`) | all apps + pipeline (auto-captured from the OpenAI deployment in Part 6) |
| `blob-conn` (`AZURE_BLOB_CONNECTION_STRING`) | dashboard, chatbot (auto-captured from the storage account in Part 3) |
| `chat-jwt-secret` (`CHAT_JWT_SECRET`) | dashboard + chatbot (**must match**, per tier) |
| `session-secret` (`APIX_SESSION_SECRET`) | dashboard |
| storage account key (`SALES_STORAGE_ACCOUNT_KEY` / for azcopy SAS generation) | pipeline, Part 4 |
| ghcr PAT | registry pull (if packages are private) |

**No SQL secret** — SQL access is managed-identity + a SQL-side grant, not a
Container App secret (see §2). Migrate the secrets above to Key Vault once you
have (or a favor from someone with) `User Access Administrator` on the resource
group.
