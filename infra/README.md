# infra/

Azure **Container Apps** deployment for the whole suite.

## Files

| File | What it is |
|---|---|
| `main.bicep` | One deployment: Container Apps Environment + Log Analytics, an Azure Files share mounted at `/data` (the shared trace/feedback sink), a user-assigned managed identity, **5 apps** (chatbot, dashboard-api, dashboard-web, ops-backend, ops-web) and **2 scheduled jobs** (pipeline weekly, chatbot ingestion weekly). |
| `main.parameters.dev.json` | Non-secret parameters (ACR, endpoints, program config). Secrets are `@secure()` and passed at deploy time — never committed here. |
| `deploy.sh` | Builds every image in ACR (`az acr build`, context = repo root so `platform/` is included) then applies the bicep. |

## Topology

| Service | Kind | Ingress |
|---|---|---|
| chatbot | App | external |
| dashboard-api | App | internal (fronted by dashboard-web) |
| dashboard-web | App | external |
| ops-backend | App | internal / Entra-protected |
| ops-web | App | external / Entra-protected |
| ai_pipeline | Job | schedule (weekly) |
| chatbot ingestion | Job | schedule (weekly, after pipeline) |

## Deploy
```bash
az login
export REASONING_MODEL_APIKEY=... AZURE_BLOB_CONNECTION_STRING=... \
       AZURE_SQL_PASSWORD=... CHAT_JWT_SECRET=... APIX_SESSION_SECRET=... ENTRA_CLIENT_SECRET=...
./deploy.sh dev <resource-group> <acr-name>
```
Then grant the managed identity `Storage Blob Data Contributor` + SQL access, and
register the dashboard-api `/api/auth/callback` URL in the Entra app registration.
Validate the template offline with `az bicep build --file main.bicep`.

**Secrets:** for production, replace the inline `@secure()` params with Key Vault
secret references (the container-app `secrets` block supports `keyVaultUrl` +
managed identity). Inline params are provided for a fast first deploy.
