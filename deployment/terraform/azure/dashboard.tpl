{
  "lenses": {
    "0": {
      "order": 0,
      "parts": {
        "0": {
          "position": {
            "x": 0,
            "y": 0,
            "rowSpan": 4,
            "colSpan": 6
          },
          "metadata": {
            "inputs": [],
            "type": "Extension/HubsExtension/PartType/MarkdownPart",
            "settings": {
              "content": {
                "settings": {
                  "content": "# MicroAgents Platform Dashboard\n## Environment: ${environment}\n### Primary Region: ${location}\n\n**Last Updated:** {{utcNow('yyyy-MM-dd HH:mm')}}",
                  "title": "Overview",
                  "subtitle": "",
                  "markdownSource": 1
                }
              }
            }
          }
        },
        "1": {
          "position": {
            "x": 6,
            "y": 0,
            "rowSpan": 4,
            "colSpan": 6
          },
          "metadata": {
            "inputs": [
              {
                "name": "ComponentId",
                "value": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.ContainerService/managedClusters/${aks_cluster}"
              }
            ],
            "type": "Extension/Microsoft_Azure_KubernetesService/PartType/ManagedClusterTilePart"
          }
        },
        "2": {
          "position": {
            "x": 0,
            "y": 4,
            "rowSpan": 6,
            "colSpan": 12
          },
          "metadata": {
            "inputs": [
              {
                "name": "resourceTypeMode",
                "isOptional": true
              },
              {
                "name": "ComponentId",
                "value": {
                  "SubscriptionId": "${subscription_id}",
                  "ResourceGroup": "${resource_group}",
                  "Name": "${aks_cluster}",
                  "ResourceId": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.ContainerService/managedClusters/${aks_cluster}"
                },
                "isOptional": true
              },
              {
                "name": "PartId",
                "value": "a0c5ff62-1038-4706-b0ad-63caf0c8780d",
                "isOptional": true
              },
              {
                "name": "Version",
                "value": "2.0",
                "isOptional": true
              },
              {
                "name": "TimeRange",
                "value": "PT1H",
                "isOptional": true
              },
              {
                "name": "DashboardId",
                "isOptional": true
              },
              {
                "name": "DraftRequestParameters",
                "isOptional": true
              }
            ],
            "type": "Extension/Microsoft_Azure_KubernetesService/PartType/ManagedClusterHealthPart"
          }
        },
        "3": {
          "position": {
            "x": 0,
            "y": 10,
            "rowSpan": 6,
            "colSpan": 6
          },
          "metadata": {
            "inputs": [
              {
                "name": "ResourceId",
                "value": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${postgres_server}"
              },
              {
                "name": "PartId",
                "value": "4c9d2c9f-2b5b-4b5b-8b5b-4b5b2c9f2c9f"
              },
              {
                "name": "TimeRange",
                "value": "PT1H"
              }
            ],
            "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
            "settings": {
              "content": {
                "settings": {
                  "chart": {
                    "metrics": [
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${postgres_server}"
                        },
                        "name": "cpu_percent",
                        "aggregationType": 4,
                        "namespace": "Microsoft.DBforPostgreSQL/flexibleServers",
                        "metricVisualization": {
                          "displayName": "CPU Percent"
                        }
                      },
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${postgres_server}"
                        },
                        "name": "memory_percent",
                        "aggregationType": 4,
                        "namespace": "Microsoft.DBforPostgreSQL/flexibleServers",
                        "metricVisualization": {
                          "displayName": "Memory Percent"
                        }
                      }
                    ],
                    "title": "PostgreSQL Metrics"
                  }
                }
              }
            }
          }
        },
        "4": {
          "position": {
            "x": 6,
            "y": 10,
            "rowSpan": 6,
            "colSpan": 6
          },
          "metadata": {
            "inputs": [
              {
                "name": "ResourceId",
                "value": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Cache/Redis/${redis_cache}"
              },
              {
                "name": "PartId",
                "value": "5d9d2c9f-2b5b-4b5b-8b5b-4b5b2c9f2c9f"
              },
              {
                "name": "TimeRange",
                "value": "PT1H"
              }
            ],
            "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
            "settings": {
              "content": {
                "settings": {
                  "chart": {
                    "metrics": [
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Cache/Redis/${redis_cache}"
                        },
                        "name": "percentProcessorTime",
                        "aggregationType": 4,
                        "namespace": "Microsoft.Cache/Redis",
                        "metricVisualization": {
                          "displayName": "CPU Usage"
                        }
                      },
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Cache/Redis/${redis_cache}"
                        },
                        "name": "usedmemorypercentage",
                        "aggregationType": 4,
                        "namespace": "Microsoft.Cache/Redis",
                        "metricVisualization": {
                          "displayName": "Memory Usage"
                        }
                      }
                    ],
                    "title": "Redis Metrics"
                  }
                }
              }
            }
          }
        },
        "5": {
          "position": {
            "x": 0,
            "y": 16,
            "rowSpan": 6,
            "colSpan": 12
          },
          "metadata": {
            "inputs": [
              {
                "name": "ResourceId",
                "value": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Storage/storageAccounts/${storage_account}"
              },
              {
                "name": "PartId",
                "value": "6d9d2c9f-2b5b-4b5b-8b5b-4b5b2c9f2c9f"
              },
              {
                "name": "TimeRange",
                "value": "PT24H"
              }
            ],
            "type": "Extension/Microsoft_Azure_Monitoring/PartType/MetricsChartPart",
            "settings": {
              "content": {
                "settings": {
                  "chart": {
                    "metrics": [
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Storage/storageAccounts/${storage_account}"
                        },
                        "name": "UsedCapacity",
                        "aggregationType": 1,
                        "namespace": "Microsoft.Storage/storageAccounts",
                        "metricVisualization": {
                          "displayName": "Used Capacity"
                        }
                      },
                      {
                        "resourceMetadata": {
                          "id": "/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Storage/storageAccounts/${storage_account}"
                        },
                        "name": "Transactions",
                        "aggregationType": 1,
                        "namespace": "Microsoft.Storage/storageAccounts",
                        "metricVisualization": {
                          "displayName": "Transactions"
                        }
                      }
                    ],
                    "title": "Storage Metrics"
                  }
                }
              }
            }
          }
        }
      }
    }
  },
  "metadata": {
    "model": {
      "timeRange": {
        "value": {
          "relative": {
            "duration": 24,
            "timeUnit": 1
          }
        },
        "type": "MsPortalFx.Composition.Configuration.ValueTypes.TimeRange"
      },
      "filterLocale": {
        "value": "en-us"
      },
      "filters": {
        "value": {
          "MsPortalFx_TimeRange": {
            "model": {
              "format": "utc",
              "granularity": "auto",
              "relative": "24h"
            },
            "displayCache": {
              "name": "UTC Time",
              "value": "Past 24 hours"
            },
            "filteredPartIds": []
          }
        }
      }
    }
  }
}