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

var contentSafetyAccountName = 'cs-${workloadName}-${environmentName}-${location}-${instance}'

// Optional — only needed if a usecase enables azure_content_safety in
// config/guardrails.yaml. The free heuristic guardrails (PII, blocklist,
// prompt injection, secret leak, max length) need no Azure resource at all.
resource contentSafetyAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: contentSafetyAccountName
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: contentSafetyAccountName
    publicNetworkAccess: 'Enabled'
  }
}

output contentSafetyAccountName string = contentSafetyAccount.name
output endpoint string = contentSafetyAccount.properties.endpoint
