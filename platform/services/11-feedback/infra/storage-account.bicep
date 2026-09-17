targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region — default only, see docs/decisions/0003-model-management-scope.md for why this is not fixed platform-wide')
param location string = 'eastus'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Tags applied to this resource — should match the tags from component 01')
param tags object = {}

// Storage account names can't contain hyphens and must be <= 24 chars —
// the CAF naming convention is adapted here rather than followed literally.
var storageAccountName = toLower('st${workloadName}${environmentName}${instance}')

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource feedbackContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'feedback'
}

output storageAccountName string = storageAccount.name
