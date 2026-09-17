targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Resource ID of the storage account cost data is exported to — any existing account works, e.g. component 11 (Feedback Loop)\'s, reused rather than provisioning a new one just for this')
param exportStorageAccountId string

@description('Blob container name the export writes to')
param exportContainerName string = 'cost-exports'

@description('Export start date — first day of a month (e.g. 2026-09-01)')
param startDate string = '2026-09-01'

@description('Export end date — Cost Management exports require a bounded recurrence period; renew this periodically')
param endDate string = '2027-09-01'

var exportName = 'export-${workloadName}-${environmentName}'

resource costExport 'Microsoft.CostManagement/exports@2023-08-01' = {
  name: exportName
  properties: {
    schedule: {
      status: 'Active'
      recurrence: 'Monthly'
      recurrencePeriod: {
        from: startDate
        to: endDate
      }
    }
    format: 'Csv'
    deliveryInfo: {
      destination: {
        resourceId: exportStorageAccountId
        container: exportContainerName
        rootFolderPath: 'cost-exports'
      }
    }
    definition: {
      type: 'ActualCost'
      timeframe: 'MonthToDate'
      dataSet: {
        granularity: 'Daily'
      }
    }
  }
}

output exportName string = costExport.name
