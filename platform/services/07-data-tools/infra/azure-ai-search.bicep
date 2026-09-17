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

var searchServiceName = 'srch-${workloadName}-${environmentName}-${location}-${instance}'

// One shared Search service hosts every client's index. Isolation between
// clients is enforced at the index level (one index per client_id, see
// docs/decisions/0007-data-tools-scope.md) — this template only provisions
// the service itself. Indexes are a data-plane concept and are created via
// scripts/provision_client_index.py, not Bicep.
resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    // Public access is acceptable for this dev build; production should move
    // this behind a private endpoint once the networking model (Phase 0) is confirmed.
    publicNetworkAccess: 'enabled'
  }
}

output searchServiceName string = searchService.name
output endpoint string = 'https://${searchService.name}.search.windows.net'
