# Azure Setup via the Portal (no CLI) — in priority order

You don't have `az` CLI on the VDI yet, so **every step here is a Portal
click-path — no commands anywhere in this document.** `infra/azure-setup.ps1`
and `DEPLOYMENT.md` still exist for later (once you have CLI access, or for
whoever does), but they are **not** what you follow right now.

**Order, matching your stated priority:**
1. **Part 1** — the Azure resources local run actually needs (Azure OpenAI,
   Storage + your real data). Container Apps, Log Analytics, and the
   Container Apps Environment are **not** needed for this and are skipped
   here on purpose.
2. **Part 2** — fill in `.env` and run locally.
3. **Part 3** — Azure Container Apps deployment, **last priority**. This is
   where your "image tag" question lives — read the short explainer at the
   top of Part 3 before doing anything else in it.

Resource group `rg-llmops-apix` is assumed to already exist (you created it).
Naming still follows `docs/DEPLOYMENT.md` §0 (`llmops-apix`, not bare `apix`).

> When any screen doesn't match what's described here (Azure's Portal UI
> shifts labels/layout over time), tell me the exact screen and field names
> you see and I'll correct this doc precisely — don't guess and move on.

---

## Part 1 — Azure resources for local run

### 1.1 Azure OpenAI (resource + a `gpt-4o-mini` deployment)

1. Portal search bar (top) → type **"Azure OpenAI"** → click the service
   (not a specific resource) → **+ Create**.
2. **Basics** tab:
   - Subscription: your subscription.
   - Resource group: `rg-llmops-apix`.
   - Region: pick one that supports Azure OpenAI — **East US** or **East US 2**
     are safe choices.
   - Name: `oai-llmops-apix-dev`.
   - Pricing tier: **Standard S0**.
3. **Review + create** → **Create**. Wait ~1 minute → **Go to resource**.
4. **Create the model deployment.** In the resource's left menu, look for
   **"Model deployments"** or **"Deployments"** — on newer Azure OpenAI
   resources this link takes you to a separate site, **Azure AI Foundry**
   (`oai.azure.com` or `ai.azure.com`), which is expected, not an error.
   - Click **+ Create new deployment** (or **+ Deploy model**).
   - Model: **gpt-4o-mini**.
   - Deployment name: `gpt-4o-mini` (keep it matching the model name — simplest).
   - Deployment type: **Standard**.
   - Tokens-per-minute rate limit: the default is fine; this is a rate ceiling,
     not a cost (billing is per token actually used).
   - Click **Create** / **Deploy**.
5. **Get the endpoint + key.** Back in the classic Azure Portal, on the
   resource's page: left menu → **"Keys and Endpoint"**.
   - Copy **KEY 1** → this is `REASONING_MODEL_APIKEY`.
   - Copy **Endpoint** → this is `REASONING_MODEL_ENDPOINT`.
   - `REASONING_MODEL_DEPLOYMENT` = `gpt-4o-mini` (the deployment name from step 4).

### 1.2 Storage account (LLMOps sink + pipeline data lake)

1. Portal search → **"Storage accounts"** → **+ Create**.
2. **Basics** tab:
   - Resource group: `rg-llmops-apix`.
   - Storage account name: `stllmopsapixdev` (all lowercase, no dashes — if
     it says the name is taken, append digits: `stllmopsapixdev01`).
   - Region: same as the OpenAI resource (not required, just simpler).
   - Performance: **Standard**.
   - Redundancy: **Locally-redundant storage (LRS)** — cheapest, fine for dev.
3. **Review + create** → **Create** → **Go to resource** once done.
4. **Create the Blob containers.** Left menu → **"Containers"** (under *Data
   storage*) → **+ Container**, and create each of these five (Public access
   level: **Private**):
   - `raw`
   - `denoised-transcripts`
   - `analysis`
   - `summary`
   - `coach-hierarchy`
5. **Create the File share** (this is the LLMOps trace/feedback sink). Left
   menu → **"File shares"** (under *Data storage*) → **+ File share**:
   - Name: `llmops-data`
   - Quota: `5` GiB
   - Click **Create**.
6. **Get the connection string.** Left menu → **"Access keys"** (under
   *Security + networking*) → click **Show** next to **key1** → copy the
   **Connection string** field.
   - This whole string → `AZURE_BLOB_CONNECTION_STRING`.
   - The account name (`stllmopsapixdev`) → `SALES_STORAGE_ACCOUNT_NAME`.
   - The **key1** value alone (not the whole connection string) →
     `SALES_STORAGE_ACCOUNT_KEY`.

### 1.3 Copy your real transcripts in (`raw` + `coach-hierarchy`) — no CLI

You have real transcripts in an existing source blob you can't share details
of with me. Two Portal-friendly ways to copy them, no `az`/`azcopy` CLI needed:

**Option A — Azure Storage Explorer (recommended if you can install it).**
This is a free Microsoft GUI app (separate small installer, not the CLI) — it
usually isn't blocked by the same policies that block CLI/MSI tooling, but if
it is, use Option B instead.
1. Download & install **Azure Storage Explorer** from Microsoft.
2. Sign in with your Azure account (top-left "Connect" icon).
3. In the left tree, find the **source** storage account → its **raw**
   (or equivalent) container → select all blobs → right-click → **Copy**.
4. Find **your new** account (`stllmopsapixdev`) → its `raw` container →
   right-click → **Paste**.
5. Repeat for the coach-hierarchy data into the `coach-hierarchy` container.

**Option B — manual download + upload via the Portal (zero installs, fine for
a modest number of files).**
1. Go to the **source** storage account in the Portal → **Storage browser**
   (left menu) → **Blob containers** → the source raw container.
2. Select the files you need → **Download**.
3. Go to **your new** account (`stllmopsapixdev`) → **Storage browser** →
   **Blob containers** → `raw` → **Upload** → pick the files you just downloaded.
4. Repeat for `coach-hierarchy`.

**To unblock local run fastest:** you don't need your *entire* transcript
history to get things working — copy just **one or two dates'** worth of
`raw/*.parquet` files first (via either option above), confirm the pipeline
runs end to end locally, then come back and copy the rest later (once you have
`azcopy`/`az` CLI, that bulk copy will be much faster than either option above).

### 1.4 Azure SQL — you already have access, plus one auth note for local run

You're reusing an existing database (not creating one) and will create your
own table there manually — unchanged from before. The one new wrinkle for
**local, non-container** runs:

The app code (`chatbot`, `application`) authenticates to Azure SQL **only**
via `DefaultAzureCredential` (Azure AD token, no username/password path at
all). Locally, that needs *some* cached Azure sign-in to draw a token from.
Without `az` CLI, you have three options — pick whichever is available to you:

1. **Wait for `az` CLI** (your current plan). Once it's installed, run `az
   login` once (browser popup) and you're done — no `.env` changes needed for
   SQL, `DefaultAzureCredential` picks it up automatically.
2. **Faster unblock, try this first:** `az` CLI is also installable via `pip`
   — no admin rights, no MSI installer, so it often works even when the
   normal installer is blocked:
   ```
   pip install azure-cli
   az login
   ```
   This *is* real `az` CLI (same tool), just installed a different way. If
   `pip install` itself is blocked too, skip to option 3.
3. **Azure PowerShell module** (if both of the above are blocked): in
   PowerShell, `Install-Module -Name Az -Scope CurrentUser` (installs to your
   user profile, no admin needed), then `Connect-AzAccount` (browser popup).
   `DefaultAzureCredential` also checks for this.

Once any one of these gives you a signed-in session, the Python apps pick it
up with zero code or `.env` changes — `AZURE_SQL_SERVER` /
`AZURE_SQL_DATABASE` / `APP_AZURE_SQL_SERVER` / `APP_AZURE_SQL_DATABASE` /
`REP_TABLE` are all you set (per `.env.example`).

---

## Part 2 — Fill in `.env` and run locally

### 2.1 The full mapping from Part 1 into `.env`

| `.env` variable | Value from |
|---|---|
| `REASONING_MODEL_ENDPOINT` | §1.1 step 5 |
| `REASONING_MODEL_APIKEY` | §1.1 step 5 (KEY 1) |
| `REASONING_MODEL_DEPLOYMENT` | `gpt-4o-mini` |
| `AZURE_BLOB_CONNECTION_STRING` | §1.2 step 6 |
| `SALES_STORAGE_ACCOUNT_NAME` | `stllmopsapixdev` |
| `SALES_STORAGE_ACCOUNT_KEY` | §1.2 step 6 (key1 alone) |
| `AZURE_BLOB_CONTAINER` | `summary` — **override the code's default `weekly-summary`**, which you didn't create. Must equal `SALES_SUMMARY_CONTAINER` so the app reads what the pipeline writes. |
| `INDEX_BLOB_PATH` / `REPORTS_PREFIX` | **leave at defaults** — legacy fallback placeholders, not read by the real week-discovery path. Nothing to set here. |
| `AZURE_SQL_SERVER` / `APP_AZURE_SQL_SERVER` | your existing server, e.g. `<server>.database.windows.net` — **set both**, chatbot's `AZURE_SQL_SERVER` does **not** fall back to `APP_AZURE_SQL_SERVER` (only `AI_PIPELINE_AZURE_SQL_*` does) |
| `AZURE_SQL_DATABASE` / `APP_AZURE_SQL_DATABASE` | your existing database name |
| `REP_TABLE` | your new table's `[schema].[name]` once you create it (chatbot only — see the dashboard caveat below) |
| `CHAT_JWT_SECRET` | any long random string — signs the token the dashboard mints for the browser to call the chatbot directly; set a placeholder even if you bypass real verification below |
| `APIX_SESSION_SECRET` | any long random string — signs your **login session cookie**. Needed either way (password login *or* SSO); not an SSO on/off switch |
| `ENTRA_TENANT_ID` / `CLIENT_ID` / `CLIENT_SECRET` / `REDIRECT_URI` | **leave all blank.** Registering an Entra app typically needs Azure AD tenant permissions, separate from RG Contributor — not worth chasing now. The password-login endpoint works fully independently of these. |
| `CHAT_AUTH_DISABLED` (chatbot only) | `true` — skips real JWT verification on the chatbot side for local dev. Still set `CHAT_JWT_SECRET` above; the dashboard's mint function returns an empty token if that's unset entirely, which breaks the chat widget's request format even with verification disabled. |
| `VITE_DASHBOARD_API_URL`, `VITE_CHAT_API_URL`, `VITE_OPS_API_URL` | **leave all blank.** These aren't Azure values — they belong in a separate small `.env` inside `application/web/` and `ops-console/web/` (Vite loads its own, not this file), and each dev server already proxies the relative default to the right backend port automatically. `VITE_CHAT_API_URL` specifically is unused dead code — the chat base URL actually comes from the server's `/api/auth/chat-token` response, not a build-time var. |

**Before you can log in at all:** `application/auth.db` starts empty. Create one
local user (do this from the `application` folder so the `backend` package
resolves):
```bash
cd usecases/apix/application
python -c "from backend.auth.local_auth import add_user; add_user('demo', 'ChangeMe123!', role='manager')"
```
Use `role='manager'`, not `'coach'` — a manager/non-coach principal with no
assigned `coach_ids` is treated as a **superuser** by the RBAC logic and sees
every employee. `'coach'` would only see employees whose `CoachID` happens to
match your username, which won't match anything real.

**Reminder (unchanged from before):** `application/backend/services/azure_sql_query.py`
hardcodes `vzw.rep_pivoted` — if your new table has a different name, the
dashboard's metrics page needs a one-line code change before it'll read from
it. Tell me the table name when you have it.

### 2.2 Run it

Follow **`docs/RUNBOOK.md`** exactly, starting at §1 (Configure — copy `.env`
into each app's folder) then §2 (the no-Docker path — you don't have Docker
either, so this is already the right section for you). That document doesn't
need any changes for your situation; it was already CLI-agnostic. Come back
here only for Azure resource creation and Container Apps.

---

## Part 3 — Azure Container Apps (LOWEST PRIORITY — do this last)

**Read this before clicking anything.** Your "it's asking for an image tag"
moment: **a Container App cannot exist without a container image already
sitting somewhere Azure can pull it from.** The Portal wizard asks for the
image up front because there's no such thing as an "empty" Container App to
create first and fill in later. This means, in order, **before** you touch the
Container Apps creation screen at all:

1. Your code needs to be in a GitHub repo that GitHub Actions can build from
   (the current public repo works; your client repo works too once transferred).
2. GitHub → your repo → **Actions** tab → **build-and-push** workflow →
   **Run workflow** button (top right) → branch `main` → **Run workflow**.
   Wait for it to finish (5–10 minutes) — this is what actually produces
   `ghcr.io/<owner>/apix-combined:latest` (and the other images). **Nothing
   before this point produces an image at all.**
3. GitHub → your profile picture → **Your profile** → **Packages** tab → open
   `apix-combined` → **Package settings** → either:
   - set it to **Public** (simplest — no credential needed in the next step), or
   - keep it **Private** and create a **classic Personal Access Token** with
     `read:packages` scope (GitHub → Settings → Developer settings → Personal
     access tokens → Tokens (classic) → Generate new token).

**Only now** does "what do I put in the image tag field" have an answer:

### 3.1 Creating the combined Container App in the Portal

1. Portal search → **"Container Apps"** → **+ Create**.
2. **Basics** tab:
   - Resource group: `rg-llmops-apix`.
   - Container app name: `ca-llmops-apix-dev`.
   - Region: same as your other resources.
   - Container Apps Environment: click **Create new** →
     - Name it `cae-llmops-apix-dev`.
     - It will also prompt for/create a Log Analytics workspace — name that
       `log-llmops-apix-dev`. (This is the one place Log Analytics gets
       created — you don't need to make it separately beforehand.)
3. **Container** tab (this is the "image tag" screen):
   - Toggle **off** "Use quickstart image".
   - **Image source**: choose **"Docker Hub or other registries"** — **not**
     "Azure Container Registry" (see `docs/DEPLOYMENT.md` for why: ACR's
     managed-identity pull setup needs a role assignment you don't have).
   - **Registry login server**: `ghcr.io`
   - **Image and tag**: `ghcr.io/<your-github-username>/apix-combined:latest`
     (this is the exact field your question was about)
   - **Username / Password**: leave blank if the package is Public (step 3
     above); otherwise your GitHub username + the PAT you created.
   - CPU/Memory: `1.0` vCPU / `2.0` Gi (this image runs 3 processes + nginx —
     see `docs/COSTING.md`).
4. **Ingress** tab:
   - Ingress: **Enabled**.
   - Ingress traffic: **Accepting traffic from anywhere**.
   - Target port: `8080`.
   - Ingress type: **HTTP**.
5. Skip **Bindings**/**Volume Mounts** for now (added after creation — §3.3).
6. **Review + create** → **Create**. Wait for deployment → **Go to resource**.

### 3.2 Environment variables + secrets

On the created app: left menu → **"Containers"** → the container → edit, or
during creation on the **Container** tab there's an **Environment variables**
section. Either way, add these (mark the ones under "Secrets" as **secret
references**, not plain values — create the secret first under **"Secrets"**
in the left menu, then pick it in the env-var's "Source: Secret ref" dropdown):

| Name | Value | Secret? |
|---|---|---|
| `APIX_ENV` | `dev` | no |
| `LLMOPS_TRACER` | `jsonl` | no |
| `LLMOPS_TRACE_FILE` | `/data/traces/trace.jsonl` | no |
| `APIX_FEEDBACK_PATH` | `/data/feedback/feedback.jsonl` | no |
| `LLMOPS_PLATFORM_ROOT` | `/app/platform` | no |
| `REASONING_MODEL_ENDPOINT` | (§1.1) | no |
| `REASONING_MODEL_DEPLOYMENT` | `gpt-4o-mini` | no |
| `REASONING_MODEL_APIKEY` | (§1.1 KEY 1) | **yes** |
| `AZURE_SQL_SERVER` / `APP_AZURE_SQL_SERVER` / `AI_PIPELINE_AZURE_SQL_SERVER` | your SQL server | no |
| `AZURE_SQL_DATABASE` / `APP_AZURE_SQL_DATABASE` / `AI_PIPELINE_AZURE_SQL_DATABASE` | your SQL database | no |
| `REP_TABLE` | your table name | no |
| `CHAT_JWT_SECRET` | a long random string | **yes** |
| `APIX_SESSION_SECRET` | a long random string | **yes** |
| `AZURE_BLOB_CONNECTION_STRING` | (§1.2) | **yes** |
| `CHAT_API_BASE_URL` | `/chatbot` (relative — same-origin) | no |
| `SALES_STORAGE_ACCOUNT_NAME` | `stllmopsapixdev` | no |
| `SALES_STORAGE_ACCOUNT_KEY` | (§1.2 key1) | **yes** |
| `PIPELINE_TRIGGER_TOKEN` | a long random string (protects `/pipeline/run`) | **yes** |

### 3.3 Managed identity + SQL grant + volume mount

1. **Identity**: left menu → **"Identity"** → **System assigned** → **On** →
   **Save**. Note the app's name (`ca-llmops-apix-dev`) — that's the display
   name you'll use in the SQL grant.
2. **SQL grant** (run in SSMS/Azure Data Studio/the Portal's SQL query editor
   against your existing database — not a CLI step):
   ```sql
   CREATE USER [ca-llmops-apix-dev] FROM EXTERNAL PROVIDER;
   ALTER ROLE db_datareader ADD MEMBER [ca-llmops-apix-dev];
   ```
3. **Mount `/data`**:
   - First, link the storage to the *environment*: go to the
     **`cae-llmops-apix-dev`** Container Apps Environment resource → left menu
     → **"Azure Files"** → **+ Add** → Storage account `stllmopsapixdev`,
     File share `llmops-data`, Access mode **Read/Write**, Name `llmops-data`
     → **Add**.
   - Then, on the **`ca-llmops-apix-dev`** app itself → left menu → look for
     **"Volumes"** or, under a new revision's container settings, **"Volume
     mounts"** → add a volume of type **Azure Files** referencing the
     `llmops-data` storage you just linked, mount path `/data`. (This requires
     creating a new revision — the Portal will prompt you through it.)

### 3.4 Verify

- `https://<app-fqdn>/health` → `ok`.
- `https://<app-fqdn>/api/health` → dashboard OK.
- `https://<app-fqdn>/chatbot/health` → chatbot OK.

### 3.5 The LLMOps ops console app (separate, optional at this stage)

Same process as §3.1–3.3 but: image `ghcr.io/<owner>/apix-ops-backend:latest`,
name `ca-llmops-dev`, target port `8100`, no managed identity needed, no SQL
grant needed — just the `/data` volume mount (§3.3's third bullet, same
environment storage link). Lower priority than the combined app since it's
monitoring, not the product itself.
