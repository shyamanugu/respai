targetScope = 'resourceGroup'

@description('Environment identifier')
@allowed(['dev', 'test', 'prod'])
param environmentName string = 'dev'

@description('Workload name used across every resource in this platform')
param workloadName string = 'llmops'

@description('Instance suffix, only incremented if multiple deployments coexist in one environment')
param instance string = '001'

@description('Monthly budget amount in USD for this resource group')
param monthlyBudgetUsd int = 500

@description('Email address(es) notified when a threshold is crossed')
param notificationEmails array = [
  'change-me@example.com'
]

@description('Budget start date — first day of a month (e.g. 2026-09-01). Consumption budgets require an explicit start.')
param startDate string = '2026-09-01'

var budgetName = 'budget-${workloadName}-${environmentName}-${instance}'

// Whether Contributor at this scope can actually create a budget is
// unconfirmed — Microsoft.Consumption/budgets/write is sometimes gated
// differently depending on the billing account model. Authored here so it's
// ready to attempt; if deployment fails with an authorization error, that
// becomes a new Phase 0 queue item, not a surprise.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: monthlyBudgetUsd
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actual_50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: notificationEmails
        thresholdType: 'Actual'
      }
      actual_80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        contactEmails: notificationEmails
        thresholdType: 'Actual'
      }
      forecasted_100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: notificationEmails
        thresholdType: 'Forecasted'
      }
    }
  }
}

output budgetName string = budget.name
