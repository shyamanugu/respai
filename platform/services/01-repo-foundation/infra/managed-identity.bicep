targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region, should match the resource group location')
param location string = resourceGroup().location

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Tags applied to the identity')
param tags object = {}

var identityName = 'id-${workloadName}-${environmentName}-${location}-${instance}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// No role assignments here by design — Contributor cannot grant them.
// Every permission this identity will eventually need is tracked in
// docs/checklist/BUILD-CHECKLIST.md (Phase 0 permission request queue)
// until an Owner / User Access Administrator applies them.

output identityName string = identity.name
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
