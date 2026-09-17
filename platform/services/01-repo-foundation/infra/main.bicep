targetScope = 'subscription'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region for all resources in this module')
param location string = 'eastus'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Owner tag value')
param owner string = 'change-me'

@description('Cost center tag value')
param costCenter string = 'change-me'

@description('Business unit tag value, reserved for per-client cost separation')
param businessUnit string = 'change-me'

// Computed locally, matching resource-group.bicep's own naming expression
// exactly, rather than read from rg.outputs.resourceGroupName below — the
// `scope` property must be calculable before any module deploys, and a
// module's output doesn't qualify even when (as here) it's just echoing a
// value computable from these same input parameters.
var resourceGroupName = 'rg-${workloadName}-${environmentName}-${location}-${instance}'

module rg 'resource-group.bicep' = {
  name: 'deploy-resource-group'
  params: {
    environmentName: environmentName
    location: location
    workloadName: workloadName
    instance: instance
    owner: owner
    costCenter: costCenter
    businessUnit: businessUnit
  }
}

module identity 'managed-identity.bicep' = {
  name: 'deploy-managed-identity'
  scope: resourceGroup(resourceGroupName)
  params: {
    environmentName: environmentName
    location: location
    workloadName: workloadName
    instance: instance
    tags: rg.outputs.tags
  }
}

output resourceGroupName string = rg.outputs.resourceGroupName
output managedIdentityName string = identity.outputs.identityName
output managedIdentityPrincipalId string = identity.outputs.principalId
output managedIdentityClientId string = identity.outputs.clientId
