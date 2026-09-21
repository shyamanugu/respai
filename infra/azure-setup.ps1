<#
.SYNOPSIS
  Azure resource setup for LLMOps + APIX — Contributor-only, no Key Vault,
  ghcr.io images, managed-identity SQL auth. Companion script to
  docs/DEPLOYMENT.md — same steps, PowerShell, ready to paste into VS Code's
  integrated terminal.

.NOTES
  - Assumes the resource group already exists (rg-llmops-apix, created manually).
  - Assumes an EXISTING Azure SQL database you already have access to — this
    script does NOT create a SQL server/database.
  - Deploys APIX (ai_pipeline + application + chatbot) as ONE combined
    Container App (`ca-llmops-apix-<tier>`) — see usecases/apix/combined/ for
    why (each app keeps its own venv inside one image; a real dependency
    conflict was found when they were merged into one environment). The
    LLMOps ops console remains its own, separate app.
  - Run the whole file, or select a "# ===== PART ... =====" block and press
    F8 to run just that part.
  - Re-run for another tier later by changing $Tier to "qa"/"prod" and
    re-running from PART 1 onward (same resource group, brand-new environment,
    storage account, and OpenAI deployment for that tier).
  - The combined app's image must already be pushed to ghcr — run the
    "build-and-push" GitHub Action first (see docs/DEPLOYMENT.md Part A).
#>

# ============================================================================
# FILL THESE IN before running anything below.
# ============================================================================
$Rg    = "rg-llmops-apix"      # already created
$Tier  = "dev"                  # dev | qa | prod
$Loc   = "eastus"               # must be a region with Azure OpenAI available
$Owner = "shyamanugu"           # ghcr.io owner (lowercase)

# ghcr pull credential — leave both blank if your apix-* / llmops-* packages are Public
$GhcrUsername = ""
$GhcrPat      = ""              # classic PAT with read:packages scope

# Your EXISTING Azure SQL (not created by this script)
$AzureSqlServer   = ""          # <server>.database.windows.net
$AzureSqlDatabase = ""
$RepTable         = ""          # your new table's [schema].[name] once you create it manually (see PART 5)

# Blob connection string — captured automatically at the end of PART 3
$BlobConnectionString = ""

# Shared secrets — any long random strings, must match across whatever reads them
$ChatJwtSecret        = ""
$SessionSecret        = ""
$PipelineTriggerToken = ""      # protects the combined app's /pipeline/* endpoints

Write-Host "Resource group: $Rg | Tier: $Tier" -ForegroundColor Cyan

function Set-GhcrPull($AppName) {
    if ($GhcrUsername -and $GhcrPat) {
        az containerapp registry set -g $Rg -n $AppName --server ghcr.io --username $GhcrUsername --password $GhcrPat
    }
}

# ============================================================================
# Derived names — do not edit. "llmops-apix" is the workload token everywhere
# (not bare "apix") since this is the LLMOps platform's APIX deployment.
# ============================================================================
$Cae        = "cae-llmops-apix-$Tier"
$Law        = "log-llmops-apix-$Tier"
$St         = "stllmopsapix$Tier"     # no dashes; if taken globally, append digits, e.g. stllmopsapixdev01
$Share      = "llmops-data"
$Oai        = "oai-llmops-apix-$Tier"
$OaiDeployment = "gpt-4o-mini"
$ApixApp    = "ca-llmops-apix-$Tier"  # the ONE combined app: ai_pipeline + application + chatbot
$OpsApp     = "ca-llmops-$Tier"       # the separate LLMOps ops console (not "APIX" — no rename needed)
$PipelineJob = "caj-llmops-apix-pipeline-$Tier"  # optional scheduled job, reuses the SAME image

# ============================================================================
# PART 1 — Log Analytics workspace (this tier), capped for cost control
# ============================================================================
az monitor log-analytics workspace create -g $Rg -n $Law -l $Loc
$LawId  = az monitor log-analytics workspace show -g $Rg -n $Law --query customerId -o tsv
$LawKey = az monitor log-analytics workspace get-shared-keys -g $Rg -n $Law --query primarySharedKey -o tsv
az monitor log-analytics workspace update -g $Rg -n $Law --quota 1   # 1 GB/day cap — no fixed fee otherwise

# ============================================================================
# PART 2 — Container Apps Environment (this tier, Consumption -> scale to zero)
# ============================================================================
az containerapp env create -g $Rg -n $Cae -l $Loc --logs-workspace-id $LawId --logs-workspace-key $LawKey

# ============================================================================
# PART 3 — Storage account: the LLMOps trace/feedback sink (Azure Files) AND
# the pipeline's data-lake containers (Blob), in ONE account to keep this minimal.
# ============================================================================
az storage account create -g $Rg -n $St -l $Loc --sku Standard_LRS --kind StorageV2
# If the name is taken globally, edit $St above and re-run Part 3.
$StKey = az storage account keys list -g $Rg -n $St --query "[0].value" -o tsv
$BlobConnectionString = "DefaultEndpointsProtocol=https;AccountName=$St;AccountKey=$StKey;EndpointSuffix=core.windows.net"

az storage share-rm create -g $Rg --storage-account $St -n $Share --quota 5
az containerapp env storage set -g $Rg -n $Cae `
    --storage-name $Share --azure-file-account-name $St `
    --azure-file-account-key $StKey --azure-file-share-name $Share --access-mode ReadWrite

# Pipeline data-lake containers. raw / coach-hierarchy are INPUTS you populate
# (PART 4 copies them from your existing source blob); denoised / analysis /
# summary are OUTPUTS the pipeline writes itself when it runs. `summary`
# doubles as the app's AZURE_BLOB_CONTAINER (same container, two var names —
# see docs/.env.example's [MUST MATCH] note).
foreach ($c in @("raw", "denoised-transcripts", "analysis", "summary", "coach-hierarchy")) {
    az storage container create --account-name $St --account-key $StKey --name $c
}
Write-Host "Blob connection string captured into `$BlobConnectionString (used below)." -ForegroundColor Green

# ============================================================================
# PART 4 — Copy your real transcripts into the new storage (raw + coach-hierarchy)
# ============================================================================
# Fill these in yourself — a full blob URL including a SAS token with at least
# read+list permission on the SOURCE container.
$SourceRawUrl   = ""   # e.g. "https://<source-account>.blob.core.windows.net/<container>?<SAS>"
$SourceCoachUrl = ""

# Install azcopy once if you don't have it: winget install Microsoft.Azcopy

$expiry = (Get-Date).AddDays(7).ToString("yyyy-MM-ddTHH:mmZ")
$DestRawSas   = az storage container generate-sas --account-name $St --account-key $StKey --name raw --permissions rwl --expiry $expiry -o tsv
$DestCoachSas = az storage container generate-sas --account-name $St --account-key $StKey --name coach-hierarchy --permissions rwl --expiry $expiry -o tsv

if ($SourceRawUrl)   { azcopy sync $SourceRawUrl   "https://$St.blob.core.windows.net/raw?$DestRawSas" --recursive }
if ($SourceCoachUrl) { azcopy sync $SourceCoachUrl "https://$St.blob.core.windows.net/coach-hierarchy?$DestCoachSas" --recursive }
# `sync` (not `copy`) so re-running this later only transfers what changed.

# ============================================================================
# PART 5 — Azure SQL: you're reusing an existing database. Create your own new
# table(s) there MANUALLY, then:
#   1. Set $RepTable above to its [schema].[name].
#   2. CHATBOT reads it via REP_TABLE (already wired, no code change).
#   3. DASHBOARD hardcodes `vzw.rep_pivoted` in
#      usecases/apix/application/backend/services/azure_sql_query.py — tell me
#      your table name and I'll make that one-line edit before you rely on it.
#   4. PIPELINE resolves its query per-program config
#      (usecases/apix/ai_pipeline/programs_config/<program>/) — point that at
#      your new table the same way.
# ============================================================================

# ============================================================================
# PART 6 — Azure OpenAI: create the resource + a gpt-4o-mini deployment
# ============================================================================
az cognitiveservices account create -g $Rg -n $Oai -l $Loc `
    --kind OpenAI --sku S0 --custom-domain $Oai
# Quota/access-not-granted errors here are an Azure OpenAI approval issue on
# the subscription, not something Contributor can fix — tell me the exact error.

az cognitiveservices account deployment create -g $Rg -n $Oai `
    --deployment-name $OaiDeployment `
    --model-name $OaiDeployment --model-version "2024-07-18" --model-format OpenAI `
    --sku-name Standard --sku-capacity 10

$ReasoningEndpoint   = az cognitiveservices account show -g $Rg -n $Oai --query properties.endpoint -o tsv
$ReasoningApiKey     = az cognitiveservices account keys list -g $Rg -n $Oai --query key1 -o tsv
$ReasoningDeployment = $OaiDeployment
Write-Host "Azure OpenAI ready: $ReasoningEndpoint  (deployment: $ReasoningDeployment)" -ForegroundColor Green

# ============================================================================
# PART 7 — LLMOps ops console  ->  ca-llmops-$Tier  (no SQL, no identity needed)
# ============================================================================
az containerapp create -g $Rg -n $OpsApp --environment $Cae `
    --image "ghcr.io/$Owner/apix-ops-backend:latest" `
    --ingress external --target-port 8100 `
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi `
    --env-vars `
        "APIX_ENV=$Tier" `
        "LLMOPS_TRACER=jsonl" `
        "LLMOPS_TRACE_FILE=/data/traces/trace.jsonl" `
        "APIX_FEEDBACK_PATH=/data/feedback/feedback.jsonl" `
        "APIX_EVAL_HISTORY_PATH=/data/eval/eval_runs.jsonl" `
        "OPS_DB_PATH=/data/ops/ops.db" `
        "OPS_CORS_ORIGINS=*"
Set-GhcrPull $OpsApp
$OpsFqdn = az containerapp show -g $Rg -n $OpsApp --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Ops console: https://$OpsFqdn  (health: /healthz)" -ForegroundColor Green

# ============================================================================
# PART 8 — APIX combined app  ->  ca-llmops-apix-$Tier
# One image (usecases/apix/combined/), ai_pipeline + application + chatbot,
# each in its own venv inside the container, nginx routing by path on 8080.
# ============================================================================
az containerapp create -g $Rg -n $ApixApp --environment $Cae `
    --image "ghcr.io/$Owner/apix-combined:latest" `
    --ingress external --target-port 8080 `
    --min-replicas 0 --max-replicas 1 --cpu 1.0 --memory 2.0Gi `
    --secrets `
        "reasoning-api-key=$ReasoningApiKey" `
        "chat-jwt-secret=$ChatJwtSecret" `
        "session-secret=$SessionSecret" `
        "blob-conn=$BlobConnectionString" `
        "sales-storage-key=$StKey" `
        "pipeline-trigger-token=$PipelineTriggerToken" `
    --env-vars `
        "APIX_ENV=$Tier" `
        "LLMOPS_TRACER=jsonl" `
        "LLMOPS_TRACE_FILE=/data/traces/trace.jsonl" `
        "APIX_FEEDBACK_PATH=/data/feedback/feedback.jsonl" `
        "LLMOPS_PLATFORM_ROOT=/app/platform" `
        "REASONING_MODEL_ENDPOINT=$ReasoningEndpoint" `
        "REASONING_MODEL_DEPLOYMENT=$ReasoningDeployment" `
        "REASONING_MODEL_APIKEY=secretref:reasoning-api-key" `
        "AZURE_SQL_SERVER=$AzureSqlServer" `
        "AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "REP_TABLE=$RepTable" `
        "APP_AZURE_SQL_SERVER=$AzureSqlServer" `
        "APP_AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "AI_PIPELINE_AZURE_SQL_SERVER=$AzureSqlServer" `
        "AI_PIPELINE_AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "CHAT_JWT_SECRET=secretref:chat-jwt-secret" `
        "APIX_SESSION_SECRET=secretref:session-secret" `
        "AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn" `
        "CHAT_API_BASE_URL=/chatbot" `
        "SALES_STORAGE_ACCOUNT_NAME=$St" `
        "SALES_STORAGE_ACCOUNT_KEY=secretref:sales-storage-key" `
        "PIPELINE_TRIGGER_TOKEN=secretref:pipeline-trigger-token"
        # APIX_SPA_ORIGINS deliberately not set — everything is same-origin
        # behind this one app's nginx now, so browser CORS never applies here.
Set-GhcrPull $ApixApp

az containerapp identity assign -g $Rg -n $ApixApp --system-assigned
$ApixFqdn = az containerapp show -g $Rg -n $ApixApp --query properties.configuration.ingress.fqdn -o tsv
Write-Host "APIX (combined): https://$ApixFqdn" -ForegroundColor Green
Write-Host "Its SQL identity display name (for the T-SQL grant) = $ApixApp" -ForegroundColor Yellow

# ============================================================================
# PART 9 (optional) — scheduled pipeline job, reusing the SAME combined image
# but bypassing supervisord/nginx entirely — runs the pipeline CLI directly in
# its own venv. Use this only if you want an automated weekly run in addition
# to the always-on app's /pipeline/run endpoint; skip it otherwise.
# ============================================================================
az containerapp job create -g $Rg -n $PipelineJob --environment $Cae `
    --trigger-type Manual --replica-timeout 3600 --replica-retry-limit 1 `
    --image "ghcr.io/$Owner/apix-combined:latest" `
    --command "/opt/venv-pipeline/bin/python" "-m" "ai_pipeline.main" "--mode" "telesales" `
    --cpu 0.5 --memory 1.0Gi `
    --secrets `
        "reasoning-api-key=$ReasoningApiKey" `
        "sales-storage-key=$StKey" `
    --env-vars `
        "APIX_ENV=$Tier" `
        "LLMOPS_TRACER=jsonl" `
        "LLMOPS_TRACE_FILE=/data/traces/trace.jsonl" `
        "LLMOPS_PLATFORM_ROOT=/app/platform" `
        "SALES_STORAGE_ACCOUNT_NAME=$St" `
        "SALES_STORAGE_ACCOUNT_KEY=secretref:sales-storage-key" `
        "AI_PIPELINE_AZURE_SQL_SERVER=$AzureSqlServer" `
        "AI_PIPELINE_AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "REASONING_MODEL_ENDPOINT=$ReasoningEndpoint" `
        "REASONING_MODEL_DEPLOYMENT=$ReasoningDeployment" `
        "REASONING_MODEL_APIKEY=secretref:reasoning-api-key"
az containerapp job identity assign -g $Rg -n $PipelineJob --system-assigned
Write-Host "Pipeline job's SQL identity display name (for the T-SQL grant) = $PipelineJob" -ForegroundColor Yellow

# Start a run once you're ready (needs raw/*.parquet in place from PART 4):
#   az containerapp job start -g $Rg -n $PipelineJob --args "--mode" "telesales" "--date" "2025-08-28"

# ============================================================================
# PART 10 — Grant SQL access to the combined app's identity (and the optional
# job's, if you created it) — run in SSMS / Azure Data Studio / the Portal
# query editor against your EXISTING database. This is a SQL permission
# grant, not an Azure RBAC role assignment, so Contributor-only access doesn't
# block it — you just need SQL access, which you said you have:
#
#   CREATE USER [ca-llmops-apix-dev] FROM EXTERNAL PROVIDER;
#   ALTER ROLE db_datareader ADD MEMBER [ca-llmops-apix-dev];
#
#   -- only if you created the optional Part 9 job:
#   CREATE USER [caj-llmops-apix-pipeline-dev] FROM EXTERNAL PROVIDER;
#   ALTER ROLE db_datareader ADD MEMBER [caj-llmops-apix-pipeline-dev];
#
# (swap "dev" if $Tier isn't "dev"). Requires the database to already have an
# Azure AD admin configured, and you to be connecting as that admin (or another
# account permitted to create external-provider users).
# ============================================================================

# ============================================================================
# PART 11 — Mounting the shared /data volume (ops console + the combined app;
# also the optional job if you created it)
# ============================================================================
# No simple flag for this yet. Do it once per app, right here in VS Code:
#
#   az containerapp show -g $Rg -n $OpsApp -o yaml > ops.yaml
#   code ops.yaml
#
# Under `properties.template`, add:
#   volumes:
#     - name: data
#       storageType: AzureFile
#       storageName: llmops-data
# Under `properties.template.containers[0]`, add:
#   volumeMounts:
#     - volumeName: data
#       mountPath: /data
#
# Save, then:  az containerapp update -g $Rg -n $OpsApp --yaml ops.yaml
# Repeat for $ApixApp, and for $PipelineJob if created (use
# `containerapp job show/update` instead of `containerapp show/update`).

Write-Host "`nDone with Parts 1-9. Do Parts 10 (SQL grant) and 11 (volume mounts) next, then verify:" -ForegroundColor Yellow
Write-Host "  Invoke-RestMethod https://$OpsFqdn/healthz"
Write-Host "  Invoke-RestMethod https://$ApixFqdn/health"
Write-Host "  Invoke-RestMethod https://$ApixFqdn/api/health"
Write-Host "  Invoke-RestMethod https://$ApixFqdn/chatbot/health"
