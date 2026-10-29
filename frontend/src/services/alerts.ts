import type {
  Alert,
  AlertEvidence,
  AlertLifecycleStatus,
  AlertListResponse,
  AlertStatusHistoryResponse,
} from '../types/alert'
import type { SeverityLevel } from '../types/security'
import { request } from './api'
import { API_V1_PREFIX } from './config'

const STATUSES: readonly AlertLifecycleStatus[] = ['open', 'acknowledged', 'investigating', 'resolved', 'false_positive']
const SEVERITIES: readonly SeverityLevel[] = ['low', 'medium', 'high', 'critical']

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

/** `evidence` is a semi-structured JSON blob (see app.services.
 * alert_generation.build_alert_draft) -- validated loosely, as an
 * object, rather than field-by-field; its content is only ever read
 * for display, never trusted for a security decision. */
function isAlertEvidence(value: unknown): value is AlertEvidence {
  return typeof value === 'object' && value !== null
}

function isAlert(value: unknown): value is Alert {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    typeof value.id === 'string' &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
    'row_number' in value &&
    isCount(value.row_number) &&
    'detection_result_id' in value &&
    isNullableString(value.detection_result_id) &&
    'anomaly_result_id' in value &&
    isNullableString(value.anomaly_result_id) &&
    'hybrid_result_id' in value &&
    isNullableString(value.hybrid_result_id) &&
    'risk_assessment_id' in value &&
    isNullableString(value.risk_assessment_id) &&
    'explanation_id' in value &&
    isNullableString(value.explanation_id) &&
    'title' in value &&
    typeof value.title === 'string' &&
    'description' in value &&
    typeof value.description === 'string' &&
    'severity' in value &&
    typeof value.severity === 'string' &&
    (SEVERITIES as readonly string[]).includes(value.severity) &&
    'status' in value &&
    typeof value.status === 'string' &&
    (STATUSES as readonly string[]).includes(value.status) &&
    'evidence' in value &&
    isAlertEvidence(value.evidence) &&
    'created_at' in value &&
    typeof value.created_at === 'string' &&
    'updated_at' in value &&
    typeof value.updated_at === 'string'
  )
}

function isAlertListResponse(value: unknown): value is AlertListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'items' in value &&
    Array.isArray(value.items) &&
    value.items.every(isAlert) &&
    'page' in value &&
    isCount(value.page) &&
    'page_size' in value &&
    isCount(value.page_size) &&
    'total_items' in value &&
    isCount(value.total_items) &&
    'total_pages' in value &&
    isCount(value.total_pages)
  )
}

function isAlertStatusHistoryResponse(value: unknown): value is AlertStatusHistoryResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'alert_id' in value &&
    typeof value.alert_id === 'string' &&
    'items' in value &&
    Array.isArray(value.items)
  )
}

export interface AlertListFilters {
  status?: AlertLifecycleStatus
  severity?: SeverityLevel
  batchId?: string
  search?: string
}

/** GET /api/v1/alerts — one page of persisted alerts.
 *
 * `search` filters client-side-unsupported-server-side text matching is
 * not implemented by the backend, so this only forwards the filters the
 * API genuinely supports (`status`/`severity`/`batch_id`); a caller
 * wanting free-text search filters the already-loaded page itself (see
 * `useAlerts`), the same documented page-scoped-filtering convention
 * this project already uses for detection results.
 */
export function getAlerts(
  page: number,
  pageSize: number,
  filters: AlertListFilters = {},
  signal?: AbortSignal,
): Promise<AlertListResponse> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (filters.status) query.set('status', filters.status)
  if (filters.severity) query.set('severity', filters.severity)
  if (filters.batchId) query.set('batch_id', filters.batchId)

  return request(`${API_V1_PREFIX}/alerts?${query.toString()}`, { validate: isAlertListResponse, signal })
}

/** GET /api/v1/alerts/{alertId} */
export function getAlert(alertId: string, signal?: AbortSignal): Promise<Alert> {
  return request(`${API_V1_PREFIX}/alerts/${encodeURIComponent(alertId)}`, { validate: isAlert, signal })
}

/** GET /api/v1/alerts/{alertId}/history */
export function getAlertHistory(alertId: string, signal?: AbortSignal): Promise<AlertStatusHistoryResponse> {
  return request(`${API_V1_PREFIX}/alerts/${encodeURIComponent(alertId)}/history`, {
    validate: isAlertStatusHistoryResponse,
    signal,
  })
}

/** PATCH /api/v1/alerts/{alertId}/status — requires Security Analyst or Administrator. */
export function updateAlertStatus(
  alertId: string,
  status: AlertLifecycleStatus,
  note?: string,
  signal?: AbortSignal,
): Promise<Alert> {
  return request(`${API_V1_PREFIX}/alerts/${encodeURIComponent(alertId)}/status`, {
    method: 'PATCH',
    body: note ? { status, note } : { status },
    validate: isAlert,
    signal,
  })
}
