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

@description('Log Analytics workspace customer ID (component 05) — obtain via az monitor log-analytics workspace show')
param logAnalyticsCustomerId string

@description('Log Analytics workspace shared key (component 05) — obtain via az monitor log-analytics workspace get-shared-keys; never commit the real value')
@secure()
param logAnalyticsSharedKey string

var environmentResourceName = 'cae-${workloadName}-${environmentName}-${location}-${instance}'

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: environmentResourceName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

output environmentId string = containerAppsEnvironment.id
output environmentName string = containerAppsEnvironment.name
