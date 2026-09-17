// APIX + LLMOps — Azure Container Apps deployment.
// Creates: Log Analytics, a Container Apps Environment with an Azure Files share
// mounted at /data (the shared trace/feedback sink), a user-assigned managed
// identity, five Container Apps, and two Jobs (pipeline + chatbot ingestion).
//
// Deploy:  az deployment group create -g <rg> -f infra/main.bicep -p @infra/main.parameters.dev.json imageTag=<tag>
targetScope = 'resourceGroup'

@description('Azure region')
param location string = resourceGroup().location
@description('Short environment name, e.g. dev/test/prod')
param envName string = 'dev'
@description('ACR login server, e.g. myregistry.azurecr.io')
param acrLoginServer string
@description('Image tag (git sha) applied to every image')
param imageTag string = 'latest'

// ── Secrets (pass securely; or wire to Key Vault) ────────────────────────────
@secure()
param reasoningApiKey string = ''
@secure()
param blobConnectionString string = ''
@secure()
param sqlPassword string = ''
@secure()
param chatJwtSecret string = ''
@secure()
param sessionSecret string = ''
@secure()
param entraClientSecret string = ''

// ── Non-secret shared config (from your .env) ────────────────────────────────
param reasoningEndpoint string = ''
param reasoningDeployment string = ''
param azureSqlServer string = ''
param azureSqlDatabase string = ''
param entraTenantId string = ''
param entraClientId string = ''
param spaBaseUrl string = ''
param chatApiBaseUrl string = ''

var prefix = 'apix-${envName}'
var shareName = 'llmops-data'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: toLower(replace('${prefix}data', '-', ''))
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
}
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareName
  properties: { shareQuota: 5 }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-mi'
  location: location
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: shareName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: shareName
      accessMode: 'ReadWrite'
    }
  }
}

// Secrets shared to every app/job.
var sharedSecrets = [
  { name: 'reasoning-api-key', value: reasoningApiKey }
  { name: 'blob-conn', value: blobConnectionString }
  { name: 'sql-password', value: sqlPassword }
  { name: 'chat-jwt-secret', value: chatJwtSecret }
  { name: 'session-secret', value: sessionSecret }
  { name: 'entra-secret', value: entraClientSecret }
]

// Common non-secret env vars.
var commonEnv = [
  { name: 'APIX_ENV', value: envName }
  { name: 'LLMOPS_TRACER', value: 'jsonl' }
  { name: 'LLMOPS_TRACE_FILE', value: '/data/traces/trace.jsonl' }
  { name: 'APIX_FEEDBACK_PATH', value: '/data/feedback/feedback.jsonl' }
  { name: 'APIX_EVAL_HISTORY_PATH', value: '/data/eval/eval_runs.jsonl' }
  { name: 'LLMOPS_PLATFORM_ROOT', value: '/app/platform' }
  { name: 'REASONING_MODEL_ENDPOINT', value: reasoningEndpoint }
  { name: 'REASONING_MODEL_DEPLOYMENT', value: reasoningDeployment }
  { name: 'REASONING_MODEL_APIKEY', secretRef: 'reasoning-api-key' }
  { name: 'AZURE_BLOB_CONNECTION_STRING', secretRef: 'blob-conn' }
  { name: 'APP_AZURE_SQL_SERVER', value: azureSqlServer }
  { name: 'APP_AZURE_SQL_DATABASE', value: azureSqlDatabase }
  { name: 'AZURE_SQL_SERVER', value: azureSqlServer }
  { name: 'AZURE_SQL_DATABASE', value: azureSqlDatabase }
  { name: 'AZURE_SQL_PASSWORD', secretRef: 'sql-password' }
  { name: 'CHAT_JWT_SECRET', secretRef: 'chat-jwt-secret' }
  { name: 'APIX_SESSION_SECRET', secretRef: 'session-secret' }
  { name: 'ENTRA_TENANT_ID', value: entraTenantId }
  { name: 'ENTRA_CLIENT_ID', value: entraClientId }
  { name: 'ENTRA_CLIENT_SECRET', secretRef: 'entra-secret' }
  { name: 'CHAT_API_BASE_URL', value: chatApiBaseUrl }
  { name: 'APIX_SPA_ORIGINS', value: spaBaseUrl }
]

// Apps: name / image / port / external ingress.
var apps = [
  { name: 'chatbot', image: 'apix-chatbot', port: 8000, external: true, dataVol: true }
  { name: 'dashboard-api', image: 'apix-dashboard-api', port: 8000, external: false, dataVol: true }
  { name: 'dashboard-web', image: 'apix-dashboard-web', port: 80, external: true, dataVol: false }
  { name: 'ops-backend', image: 'apix-ops-backend', port: 8100, external: false, dataVol: true }
  { name: 'ops-web', image: 'apix-ops-console-web', port: 80, external: true, dataVol: false }
]

resource containerApps 'Microsoft.App/containerApps@2024-03-01' = [for app in apps: {
  name: '${prefix}-${app.name}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: app.external
        targetPort: app.port
        transport: 'auto'
      }
      secrets: sharedSecrets
      registries: [ { server: acrLoginServer, identity: identity.id } ]
    }
    template: {
      containers: [ {
        name: app.name
        image: '${acrLoginServer}/${app.image}:${imageTag}'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: commonEnv
        volumeMounts: app.dataVol ? [ { volumeName: 'data', mountPath: '/data' } ] : []
      } ]
      volumes: app.dataVol ? [ { name: 'data', storageType: 'AzureFile', storageName: shareName } ] : []
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
  dependsOn: [ envStorage ]
}]

// Jobs: pipeline (weekly) + chatbot ingestion (weekly, after pipeline).
resource pipelineJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-pipeline'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: '0 6 * * 1', parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: 3600
      secrets: sharedSecrets
      registries: [ { server: acrLoginServer, identity: identity.id } ]
    }
    template: {
      containers: [ {
        name: 'pipeline'
        image: '${acrLoginServer}/apix-pipeline:${imageTag}'
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: commonEnv
        volumeMounts: [ { volumeName: 'data', mountPath: '/data' } ]
      } ]
      volumes: [ { name: 'data', storageType: 'AzureFile', storageName: shareName } ]
    }
  }
  dependsOn: [ envStorage ]
}

resource ingestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-chatbot-ingest'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: '0 8 * * 1', parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: 1800
      secrets: sharedSecrets
      registries: [ { server: acrLoginServer, identity: identity.id } ]
    }
    template: {
      containers: [ {
        name: 'chatbot-ingest'
        image: '${acrLoginServer}/apix-chatbot:${imageTag}'
        command: [ 'python', '-m', 'chatbot.ingestion' ]
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: commonEnv
        volumeMounts: [ { volumeName: 'data', mountPath: '/data' } ]
      } ]
      volumes: [ { name: 'data', storageType: 'AzureFile', storageName: shareName } ]
    }
  }
  dependsOn: [ envStorage ]
}

output identityPrincipalId string = identity.properties.principalId
output environmentId string = env.id
