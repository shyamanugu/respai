#!/usr/bin/env bash
# Build every image in ACR and deploy the Container Apps stack.
#
# Prereqs: az login; an ACR and a resource group; secrets exported as env vars.
# Usage:   ./infra/deploy.sh <env> <resource-group> <acr-name>
#   e.g.   ./infra/deploy.sh dev apix-rg apixregistry
#
# Secrets are read from the environment (never committed):
#   REASONING_MODEL_APIKEY AZURE_BLOB_CONNECTION_STRING AZURE_SQL_PASSWORD
#   CHAT_JWT_SECRET APIX_SESSION_SECRET ENTRA_CLIENT_SECRET
set -euo pipefail

ENVN="${1:-dev}"
RG="${2:?resource group required}"
ACR="${3:?acr name required}"
TAG="$(git rev-parse --short HEAD 2>/dev/null || echo latest)"
ACR_SERVER="$(az acr show -n "$ACR" --query loginServer -o tsv)"

echo "==> Building images in ACR $ACR (tag $TAG) — context = repo root"
declare -A IMAGES=(
  [apix-pipeline]="usecases/apix/ai_pipeline/Dockerfile"
  [apix-chatbot]="usecases/apix/chatbot/Dockerfile"
  [apix-dashboard-api]="usecases/apix/application/Dockerfile"
  [apix-dashboard-web]="usecases/apix/application/web/Dockerfile"
  [apix-ops-backend]="ops-console/backend/Dockerfile"
  [apix-ops-console-web]="ops-console/web/Dockerfile"
)
for img in "${!IMAGES[@]}"; do
  echo "    - $img (${IMAGES[$img]})"
  az acr build -r "$ACR" -t "${img}:${TAG}" -f "${IMAGES[$img]}" .
done

echo "==> Deploying bicep to resource group $RG"
az deployment group create \
  -g "$RG" \
  -f infra/main.bicep \
  -p "@infra/main.parameters.${ENVN}.json" \
  -p envName="$ENVN" acrLoginServer="$ACR_SERVER" imageTag="$TAG" \
  -p reasoningApiKey="${REASONING_MODEL_APIKEY:-}" \
     blobConnectionString="${AZURE_BLOB_CONNECTION_STRING:-}" \
     sqlPassword="${AZURE_SQL_PASSWORD:-}" \
     chatJwtSecret="${CHAT_JWT_SECRET:-}" \
     sessionSecret="${APIX_SESSION_SECRET:-}" \
     entraClientSecret="${ENTRA_CLIENT_SECRET:-}"

echo "==> Done. Grant the managed identity Storage Blob Data Contributor + SQL access,"
echo "    then browse the dashboard-web and ops-web ingress URLs (az containerapp show ...)."
