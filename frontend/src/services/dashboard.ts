import type { DashboardOverview } from '../types/dashboard'
import { request } from './api'
import { API_V1_PREFIX } from './config'

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isSeverityDistribution(value: unknown): value is DashboardOverview['alert_severity_distribution'] {
  return (
    typeof value === 'object' &&
    value !== null &&
    'low' in value &&
    isCount(value.low) &&
    'medium' in value &&
    isCount(value.medium) &&
    'high' in value &&
    isCount(value.high) &&
    'critical' in value &&
    isCount(value.critical)
  )
}

function isDashboardOverview(value: unknown): value is DashboardOverview {
  return (
    typeof value === 'object' &&
    value !== null &&
    'total_batches' in value &&
    isCount(value.total_batches) &&
    'total_detection_results' in value &&
    isCount(value.total_detection_results) &&
    'total_anomaly_results' in value &&
    isCount(value.total_anomaly_results) &&
    'total_alerts' in value &&
    isCount(value.total_alerts) &&
    'open_alerts' in value &&
    isCount(value.open_alerts) &&
    'alert_severity_distribution' in value &&
    isSeverityDistribution(value.alert_severity_distribution) &&
    'active_capture_sessions' in value &&
    isCount(value.active_capture_sessions)
  )
}

/** GET /api/v1/dashboard/overview — real, measured SOC metrics. */
export function getDashboardOverview(signal?: AbortSignal): Promise<DashboardOverview> {
  return request(`${API_V1_PREFIX}/dashboard/overview`, { validate: isDashboardOverview, signal })
}
