<#
.SYNOPSIS
  Azure resource setup for APIX + LLMOps — Contributor-only, no Key Vault,
  ghcr.io images, managed-identity SQL auth. Companion script to
  docs/DEPLOYMENT.md — same steps, PowerShell, ready to paste into VS Code's
  integrated terminal.

.NOTES
  - Assumes the resource group already exists (rg-llmops-apix, created manually).
  - Assumes an EXISTING Azure SQL database you already have access to (per your
    call: reuse it, create your own new table(s) there manually, don't touch
    the existing ones) — this script does NOT create a SQL server/database.
  - Run the whole file, or select a "# ===== PART ... =====" block and press F8
    to run just that part.
  - Re-run for another tier later by changing $Tier to "qa"/"prod" and
    re-running from PART 1 onward (same resource group, brand-new environment,
    brand-new storage account and OpenAI deployment for that tier).
  - Every `az containerapp create` needs its own image already pushed to ghcr —
    run the "build-and-push" GitHub Action first (see docs/DEPLOYMENT.md Part A).
#>

# ============================================================================
# FILL THESE IN before running anything below.
# ============================================================================
$Rg    = "rg-llmops-apix"      # already created
$Tier  = "dev"                  # dev | qa | prod
$Loc   = "eastus"               # must be a region with Azure OpenAI available
$Owner = "shyamanugu"           # ghcr.io owner (lowercase)

# ghcr pull credential — leave both blank if your apix-* packages are Public
$GhcrUsername = ""
$GhcrPat      = ""              # classic PAT with read:packages scope

# Your EXISTING Azure SQL (not created by this script)
$AzureSqlServer   = ""          # <server>.database.windows.net
$AzureSqlDatabase = ""
$RepTable         = ""          # your new table's [schema].[name] once you create it manually (see PART 5)

# Blob connection string secrets (dashboard + chatbot session storage) — set
# after PART 3 creates the storage account, or fill in now if you already know it.
$BlobConnectionString = ""      # filled in automatically at the end of PART 3

# Chatbot <-> dashboard shared auth secrets — any long random strings, must match
$ChatJwtSecret = ""
$SessionSecret = ""

Write-Host "Resource group: $Rg | Tier: $Tier" -ForegroundColor Cyan

function Set-GhcrPull($AppName) {
    if ($GhcrUsername -and $GhcrPat) {
        az containerapp registry set -g $Rg -n $AppName --server ghcr.io --username $GhcrUsername --password $GhcrPat
    }
}

# ============================================================================
# Derived names — do not edit
# ============================================================================
$Cae   = "cae-apix-$Tier"
$Law   = "log-apix-$Tier"
$St    = "stapix$Tier"          # no dashes; if taken globally, append digits, e.g. stapixdev01
$Share = "llmops-data"
$Oai   = "oai-apix-$Tier"
$OaiDeployment = "gpt-4o-mini"

# ============================================================================
# PART 1 — Log Analytics workspace (this tier), capped for cost control
# ============================================================================
az monitor log-analytics workspace create -g $Rg -n $Law -l $Loc
$LawId  = az monitor log-analytics workspace show -g $Rg -n $Law --query customerId -o tsv
$LawKey = az monitor log-analytics workspace get-shared-keys -g $Rg -n $Law --query primarySharedKey -o tsv
az monitor log-analytics workspace update -g $Rg -n $Law --quota 1   # 1 GB/day cap — Log Analytics has no fixed fee, only this to bound

# ============================================================================
# PART 2 — Container Apps Environment (this tier, Consumption -> scale to zero)
# ============================================================================
az containerapp env create -g $Rg -n $Cae -l $Loc --logs-workspace-id $LawId --logs-workspace-key $LawKey

# ============================================================================
# PART 3 — Storage account: the LLMOps trace/feedback sink (Azure Files) AND
# the pipeline's data-lake containers (Blob), in ONE account to keep this minimal.
# ============================================================================
az storage account create -g $Rg -n $St -l $Loc --sku Standard_LRS --kind StorageV2
# If the name is taken globally, edit $St above (e.g. "stapixdev01") and re-run Part 3.
$StKey = az storage account keys list -g $Rg -n $St --query "[0].value" -o tsv
$BlobConnectionString = "DefaultEndpointsProtocol=https;AccountName=$St;AccountKey=$StKey;EndpointSuffix=core.windows.net"

# LLMOps sink (Azure Files, mounted at /data in the apps)
az storage share-rm create -g $Rg --storage-account $St -n $Share --quota 5
az containerapp env storage set -g $Rg -n $Cae `
    --storage-name $Share --azure-file-account-name $St `
    --azure-file-account-key $StKey --azure-file-share-name $Share --access-mode ReadWrite

# Pipeline data-lake containers. raw / coach-hierarchy are INPUTS you populate
# (PART 4 below copies them from your existing source blob); denoised / analysis /
# summary are OUTPUTS the pipeline writes itself when it runs — just need to exist.
# `summary` doubles as the app's AZURE_BLOB_CONTAINER (same container, two var names
# — see docs/.env.example's [MUST MATCH] note).
foreach ($c in @("raw", "denoised-transcripts", "analysis", "summary", "coach-hierarchy")) {
    az storage container create --account-name $St --account-key $StKey --name $c
}

Write-Host "Blob connection string captured into `$BlobConnectionString (used below)." -ForegroundColor Green

# ============================================================================
# PART 4 — Copy your real transcripts into the new storage (raw + coach-hierarchy)
# ============================================================================
# You said you can't share the source blob's details with me — fill these two
# lines in yourself (a full blob URL including a SAS token with at least
# read+list permission on the SOURCE container; ask whoever manages that
# storage account for one, or generate it yourself via the Portal's Storage
# Browser -> Generate SAS, or `az storage container generate-sas` against the
# source account if you have its key/RBAC).
$SourceRawUrl   = ""   # e.g. "https://<source-account>.blob.core.windows.net/<container>?<SAS>"
$SourceCoachUrl = ""   # same, for the coach-hierarchy source container

# Install azcopy once if you don't have it: winget install Microsoft.Azcopy
# (or download from https://aka.ms/downloadazcopy-v10-windows)

$expiry = (Get-Date).AddDays(7).ToString("yyyy-MM-ddTHH:mmZ")
$DestRawSas   = az storage container generate-sas --account-name $St --account-key $StKey --name raw --permissions rwl --expiry $expiry -o tsv
$DestCoachSas = az storage container generate-sas --account-name $St --account-key $StKey --name coach-hierarchy --permissions rwl --expiry $expiry -o tsv

if ($SourceRawUrl) {
    azcopy sync $SourceRawUrl "https://$St.blob.core.windows.net/raw?$DestRawSas" --recursive
}
if ($SourceCoachUrl) {
    azcopy sync $SourceCoachUrl "https://$St.blob.core.windows.net/coach-hierarchy?$DestCoachSas" --recursive
}
# `azcopy sync` (not `copy`) so re-running this later only transfers what changed
# at the source — safe to re-run whenever the source gets new weekly transcripts.

# ============================================================================
# PART 5 — Azure SQL: you're reusing an existing database. Create your own new
# table(s) there MANUALLY (SSMS / Azure Data Studio / Portal query editor) —
# not scripted here, per your preference. Once you have a table:
#   1. Set $RepTable above to its [schema].[name].
#   2. The CHATBOT reads it via the REP_TABLE env var (already wired, no code
#      change needed).
#   3. The DASHBOARD hardcodes `vzw.rep_pivoted` in
#      usecases/apix/application/backend/services/azure_sql_query.py — if your
#      new table has a different name, that file needs a one-line edit before
#      the dashboard's metrics page will read from it. Tell me when you're
#      ready and I'll make that change.
#   4. The PIPELINE's individual_metrics step resolves its query per-program
#      config (usecases/apix/ai_pipeline/programs_config/<program>/) — point
#      that at your new table the same way.
# ============================================================================

# ============================================================================
# PART 6 — Azure OpenAI: create the resource + a gpt-4o-mini deployment
# ============================================================================
az cognitiveservices account create -g $Rg -n $Oai -l $Loc `
    --kind OpenAI --sku S0 --custom-domain $Oai
# If this fails with a quota/access-not-granted error, that's an Azure OpenAI
# access approval issue on the subscription, not something Contributor can fix —
# tell me the exact error.

az cognitiveservices account deployment create -g $Rg -n $Oai `
    --deployment-name $OaiDeployment `
    --model-name $OaiDeployment --model-version "2024-07-18" --model-format OpenAI `
    --sku-name Standard --sku-capacity 10
# --sku-capacity is a rate-limit ceiling (thousands of tokens/minute), not a
# cost driver — billing is per token actually used. Raise it if you get 429s.

$ReasoningEndpoint   = az cognitiveservices account show -g $Rg -n $Oai --query properties.endpoint -o tsv
$ReasoningApiKey     = az cognitiveservices account keys list -g $Rg -n $Oai --query key1 -o tsv
$ReasoningDeployment = $OaiDeployment
Write-Host "Azure OpenAI ready: $ReasoningEndpoint  (deployment: $ReasoningDeployment)" -ForegroundColor Green

# ============================================================================
# PART 7 — LLMOps ops console  ->  ca-llmops-$Tier  (no SQL, no identity needed)
# ============================================================================
$OpsApp = "ca-llmops-$Tier"
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
# PART 8 — APIX chatbot  ->  ca-apix-chatbot-$Tier  (needs a managed identity for SQL)
# ============================================================================
$ChatbotApp = "ca-apix-chatbot-$Tier"
az containerapp create -g $Rg -n $ChatbotApp --environment $Cae `
    --image "ghcr.io/$Owner/apix-chatbot:latest" `
    --ingress external --target-port 8000 `
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi `
    --secrets "reasoning-api-key=$ReasoningApiKey" "chat-jwt-secret=$ChatJwtSecret" "blob-conn=$BlobConnectionString" `
    --env-vars `
        "APIX_ENV=$Tier" `
        "LLMOPS_TRACER=jsonl" `
        "LLMOPS_TRACE_FILE=/data/traces/trace.jsonl" `
        "LLMOPS_PLATFORM_ROOT=/app/platform" `
        "REASONING_MODEL_ENDPOINT=$ReasoningEndpoint" `
        "REASONING_MODEL_DEPLOYMENT=$ReasoningDeployment" `
        "REASONING_MODEL_APIKEY=secretref:reasoning-api-key" `
        "AZURE_SQL_SERVER=$AzureSqlServer" `
        "AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "REP_TABLE=$RepTable" `
        "CHAT_JWT_SECRET=secretref:chat-jwt-secret" `
        "AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn"
Set-GhcrPull $ChatbotApp

az containerapp identity assign -g $Rg -n $ChatbotApp --system-assigned
$ChatbotFqdn = az containerapp show -g $Rg -n $ChatbotApp --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Chatbot: https://$ChatbotFqdn  (health: /health)" -ForegroundColor Green
Write-Host "Chatbot's SQL identity display name (for the T-SQL grant) = $ChatbotApp" -ForegroundColor Yellow

# ============================================================================
# PART 9 — APIX dashboard: API (internal, needs identity for SQL) then web (external)
# ============================================================================
$ApiApp = "ca-apix-api-$Tier"
$WebApp = "ca-apix-web-$Tier"

az containerapp create -g $Rg -n $ApiApp --environment $Cae `
    --image "ghcr.io/$Owner/apix-dashboard-api:latest" `
    --ingress internal --target-port 8000 `
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi `
    --secrets "reasoning-api-key=$ReasoningApiKey" "chat-jwt-secret=$ChatJwtSecret" "session-secret=$SessionSecret" "blob-conn=$BlobConnectionString" `
    --env-vars `
        "APIX_ENV=$Tier" `
        "LLMOPS_TRACER=jsonl" `
        "LLMOPS_TRACE_FILE=/data/traces/trace.jsonl" `
        "LLMOPS_PLATFORM_ROOT=/app/platform" `
        "REASONING_MODEL_ENDPOINT=$ReasoningEndpoint" `
        "REASONING_MODEL_DEPLOYMENT=$ReasoningDeployment" `
        "REASONING_MODEL_APIKEY=secretref:reasoning-api-key" `
        "APP_AZURE_SQL_SERVER=$AzureSqlServer" `
        "APP_AZURE_SQL_DATABASE=$AzureSqlDatabase" `
        "CHAT_JWT_SECRET=secretref:chat-jwt-secret" `
        "APIX_SESSION_SECRET=secretref:session-secret" `
        "AZURE_BLOB_CONNECTION_STRING=secretref:blob-conn" `
        "CHAT_API_BASE_URL=https://$ChatbotFqdn"
        # APIX_SPA_ORIGINS is set in the wire-up step below, once $WebApp exists.
Set-GhcrPull $ApiApp
az containerapp identity assign -g $Rg -n $ApiApp --system-assigned
$ApiFqdn = az containerapp show -g $Rg -n $ApiApp --query properties.configuration.ingress.fqdn -o tsv
Write-Host "Dashboard API's SQL identity display name (for the T-SQL grant) = $ApiApp" -ForegroundColor Yellow

az containerapp create -g $Rg -n $WebApp --environment $Cae `
    --image "ghcr.io/$Owner/apix-dashboard-web:latest" `
    --ingress external --target-port 80 `
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi `
    --env-vars "DASHBOARD_API_UPSTREAM=https://$ApiFqdn"
Set-GhcrPull $WebApp
$WebFqdn = az containerapp show -g $Rg -n $WebApp --query properties.configuration.ingress.fqdn -o tsv

az containerapp update -g $Rg -n $ApiApp --set-env-vars "APIX_SPA_ORIGINS=https://$WebFqdn"
Write-Host "Dashboard: https://$WebFqdn" -ForegroundColor Green

# ============================================================================
# PART 10 — Pipeline batch job (needs identity for SQL too) — optional, run when ready
# ============================================================================
$PipelineJob = "caj-apix-pipeline-$Tier"
az containerapp job create -g $Rg -n $PipelineJob --environment $Cae `
    --trigger-type Manual --replica-timeout 3600 --replica-retry-limit 1 `
    --image "ghcr.io/$Owner/apix-pipeline:latest" --cpu 0.5 --memory 1.0Gi `
    --secrets "reasoning-api-key=$ReasoningApiKey" "sales-storage-key=$StKey" `
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
# PART 11 — Grant the three identities SQL access (run in SSMS / Azure Data
# Studio / the Portal query editor against your EXISTING database — this is a
# SQL permission grant, not an Azure RBAC role assignment, so Contributor-only
# access doesn't block it; you just need SQL access, which you said you have):
#
#   CREATE USER [ca-apix-chatbot-dev]    FROM EXTERNAL PROVIDER;
#   CREATE USER [ca-apix-api-dev]        FROM EXTERNAL PROVIDER;
#   CREATE USER [caj-apix-pipeline-dev]  FROM EXTERNAL PROVIDER;
#   ALTER ROLE db_datareader ADD MEMBER [ca-apix-chatbot-dev];
#   ALTER ROLE db_datareader ADD MEMBER [ca-apix-api-dev];
#   ALTER ROLE db_datareader ADD MEMBER [caj-apix-pipeline-dev];
#
# (swap the names above if $Tier isn't "dev"). Requires the database to already
# have an Azure AD admin configured, and you to be connecting as that admin (or
# another account with permission to create external-provider users).
# ============================================================================

# ============================================================================
# PART 12 — Mounting the shared /data volume (once per app that needs it:
# ops-console, chatbot, dashboard-api, pipeline job — NOT dashboard-web)
# ============================================================================
# There's no simple flag for this yet. Do it once per app, right here in VS Code:
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
# Repeat for $ChatbotApp, $ApiApp (use `containerapp show/update`) and
# $PipelineJob (use `containerapp job show/update` instead).

Write-Host "`nDone with Parts 1-10. Do Parts 11 (SQL grant) and 12 (volume mounts) next, then verify:" -ForegroundColor Yellow
Write-Host "  Invoke-RestMethod https://$OpsFqdn/healthz"
Write-Host "  Invoke-RestMethod https://$ChatbotFqdn/health"
