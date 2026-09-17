targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Azure region — default only')
param location string = 'eastus'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Tags applied to this resource — should match the tags from component 01')
param tags object = {}

@description('Resource ID of the Container Apps managed environment — output of container-apps-environment.bicep')
param containerAppsEnvironmentId string

@description('Container image reference — placeholder until CI/CD (09) can build and push a real image once Entra ID access allows it')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Percentage of traffic sent to the latest revision during a canary rollout; 100 once a new revision is confirmed healthy')
param latestRevisionTrafficPercent int = 100

var containerAppName = 'ca-${workloadName}-${environmentName}-${location}-${instance}'

// No registry credentials configured — Managed Identity pull access
// (AcrPull role) is blocked until RBAC role assignments are approved
// (Phase 0 queue), same interim posture as every other component's Azure
// resource in this platform.
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        traffic: [
          {
            latestRevision: true
            weight: latestRevisionTrafficPercent
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'pipeline-server'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output containerAppName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
