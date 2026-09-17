targetScope = 'resourceGroup'

@description('Name of the existing Azure OpenAI account (component 03) to attach diagnostic settings to')
param openAiAccountName string

@description('Resource ID of the Log Analytics workspace (component 05) to send logs/metrics to')
param logAnalyticsWorkspaceId string

// A diagnostic setting is an extension resource — it must be scoped to the
// target resource, which means Bicep needs to know that resource's exact
// type at authoring time (via an `existing` reference). That makes one
// truly generic module across arbitrary resource types impractical to
// author with confidence; this file is a validated, concrete worked
// example for one resource type (Azure OpenAI). Every other component's
// Bicep should add its own diagnosticSettings child resource following
// this exact pattern — see docs/decisions/0016-security-compliance-scope.md
// for why a single "universal" module was rejected instead.
resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: openAiAccountName
}

resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: openAiAccount
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}
