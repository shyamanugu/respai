targetScope = 'subscription'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region for the resource group')
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

var resourceGroupName = 'rg-${workloadName}-${environmentName}-${location}-${instance}'

var tags = {
  environment: environmentName
  project: workloadName
  owner: owner
  costCenter: costCenter
  businessUnit: businessUnit
}

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

output resourceGroupName string = rg.name
output location string = rg.location
output tags object = tags
