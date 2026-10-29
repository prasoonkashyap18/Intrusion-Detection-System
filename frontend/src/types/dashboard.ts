/** Response body of GET /api/v1/dashboard/overview — every field is a
 * real, measured count; an empty system returns zeros, never an error. */
export interface SeverityDistribution {
  low: number
  medium: number
  high: number
  critical: number
}

export interface DashboardOverview {
  total_batches: number
  total_detection_results: number
  total_anomaly_results: number
  total_alerts: number
  open_alerts: number
  alert_severity_distribution: SeverityDistribution
  active_capture_sessions: number
}
