targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region — default only; per-deployment overrides may be needed later for data residency, see ADR 0003')
param location string = 'eastus'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Tags applied to this resource — should match the tags from component 01')
param tags object = {}

@description('Model used for the reason and judge aliases')
param reasonModel string = 'gpt-4o'

@description('Model used for the bulk and nano aliases')
param bulkModel string = 'gpt-4o-mini'

@description('Model used for the embedding alias')
param embeddingModel string = 'text-embedding-3-large'

var accountName = 'oai-${workloadName}-${environmentName}-${location}-${instance}'

resource account 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    // Public access is acceptable for this dev build; production should move
    // this behind a private endpoint once the networking model (Phase 0) is confirmed.
    publicNetworkAccess: 'Enabled'
  }
}

// Deployments on the same Azure OpenAI account must be created serially —
// concurrent deployment operations against one account can conflict.
resource reasonDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: account
  name: reasonModel
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: reasonModel
    }
  }
}

resource bulkDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: account
  name: bulkModel
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: bulkModel
    }
  }
  dependsOn: [
    reasonDeployment
  ]
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: account
  name: embeddingModel
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel
    }
  }
  dependsOn: [
    bulkDeployment
  ]
}

output accountName string = account.name
output endpoint string = account.properties.endpoint
